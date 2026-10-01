import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from test_regressions import functions
from momentumlab.research_audit import build_audit, export_audit
from momentumlab.live_model import select_target_rows


class ResearchAuditTests(unittest.TestCase):
    def setUp(self):
        self.ns=functions(); self.dates=pd.bdate_range('2026-08-17',periods=20); self.day=self.dates[14]
        rows=[]
        for i in range(25):
            for j,d in enumerate(self.dates):
                if i==23 and j==10: continue # Suspended ticker: engine skips absent rows.
                if i==22 and j<12: continue
                price=100 if i==24 else 100*(1+.002*i)**j*(1+.01*np.sin(j))
                rows.append(dict(ts_code=f'T{i:02}',trade_date=d,adj_close=price,close=price))
        prices=pd.DataFrame(rows)
        weights=pd.DataFrame([dict(con_code=f'T{i:02}',trade_date=self.dates[0],weight=1/24,market_cap_rmb=300000000) for i in range(1,25)]+
                            [dict(con_code='T00',trade_date=self.dates[16],weight=1,market_cap_rmb=400000000)])
        ranked=self.ns['merge_membership_and_rank'](self.ns['calculate_signal'](prices,10,calendar=self.dates),weights)
        self.market=dict(ranked=ranked,adjusted=prices,raw=prices,weights=weights,calendar=list(self.dates.strftime('%Y-%m-%d')),names={},
                         data_end=self.dates[-1].strftime('%Y-%m-%d'),provenance={'dataset_id':'fixture','dataset_fingerprint':'abc'},integrity={'status':'PASS'})
        self.strategy=dict(universe='ETF_250M',lookback=10,selection='Top 10',rebalance_days=10)

    def audit(self,ticker='T20'):
        return build_audit(self.market,self.strategy,self.day,ticker,backtest=self.ns['backtest_v4'])

    def test_retained_values_rank_target_execution_and_fingerprint(self):
        a=self.audit(); trace=a['trace']['summary']
        row=self.market['ranked'].query('ts_code == "T20" and trade_date == @self.day').iloc[0]
        self.assertEqual(trace['mom_score'],row.momentum_score)
        self.assertEqual(trace['rank'],row.momentum_rank)
        self.assertEqual(trace['daily_sd'],row.signal_daily_std)
        self.assertEqual(trace['price_observations'],11)
        self.assertEqual(trace['return_observations'],10)
        expected=select_target_rows(self.market['ranked'],'Top 10',self.day)
        self.assertEqual([r['ticker'] for r in a['top10_target']],list(expected.ts_code))
        self.assertAlmostEqual(sum(r['weight'] for r in a['top10_target']),1)
        self.assertEqual(a['manifest']['execution_date'],self.dates[15].strftime('%Y-%m-%d'))
        self.assertEqual(a['manifest']['dataset_fingerprint'],'abc')
        self.assertTrue(all(c['status']=='MATCH' for c in a['comparisons']))
        daily=[r['daily_return'] for r in a['trace']['daily_returns']]
        self.assertAlmostEqual(np.std(daily,ddof=1),trace['daily_sd'])

    def test_historical_membership_exclusions_and_suspension(self):
        a=self.audit('T23'); summary={r['ticker']:r for r in a['mom_calculation']}
        self.assertFalse(summary['T00']['eligible']) # Future membership must not leak.
        self.assertIn('not eligible',summary['T00']['exclusion_reasons'])
        self.assertIn('insufficient history',summary['T22']['exclusion_reasons'])
        self.assertIn('zero volatility',summary['T24']['exclusion_reasons'])
        self.assertIn(self.dates[10].strftime('%Y-%m-%d'),summary['T23']['missing_exchange_dates'])
        self.assertEqual(summary['T23']['return_observations'],8)
        self.assertIsNone(summary['T23']['mom_score'])
        self.assertEqual(a['manifest']['eligible_count'],24)

    def test_export_full_precision_and_components(self):
        a=self.audit()
        with tempfile.TemporaryDirectory() as root:
            path=export_audit(a,root)
            with zipfile.ZipFile(path) as z:
                self.assertEqual(len(z.namelist()),8)
                manifest=json.loads(z.read('audit_manifest.json'))
                self.assertEqual(manifest['dataset_fingerprint'],'abc')
                frame=pd.read_csv(z.open('mom_calculation.csv'),float_precision='round_trip')
                self.assertEqual(frame.set_index('ticker').loc['T20','mom_score'],a['trace']['summary']['mom_score'])

    def test_exact_ties_use_production_order(self):
        ranked=self.market['ranked']
        # Feed a retained production ranking with a genuine equal-price pair.
        prices=self.market['adjusted'].copy()
        prices.loc[prices.ts_code=='T02','adj_close']=prices.loc[prices.ts_code=='T01','adj_close'].values
        self.market['ranked']=self.ns['merge_membership_and_rank'](self.ns['calculate_signal'](prices,10,calendar=self.dates),self.market['weights'])
        a=self.audit('T01'); rows={r['ticker']:r for r in a['ranking']}
        self.assertEqual(rows['T01']['exact_ties'],'T02')
        self.assertLess(rows['T01']['rank'],rows['T02']['rank'])

    def test_closed_date_rejected_and_saved_evidence_not_inferred(self):
        with self.assertRaisesRegex(ValueError,'exact open'):
            build_audit(self.market,self.strategy,'2026-09-05')
        with tempfile.TemporaryDirectory() as root:
            a=build_audit(self.market,self.strategy,self.day,results_root=root)
            self.assertTrue(all(c['status']=='UNAVAILABLE' for c in a['comparisons'] if c['source'] in ('Research Lab','Strategy Grid')))

    def test_saved_grid_mismatch_is_reported_with_original_fingerprint(self):
        with tempfile.TemporaryDirectory() as root:
            run=Path(root)/'backtests'/'saved';run.mkdir(parents=True)
            (run/'run_metadata.json').write_text(json.dumps(dict(universe='ETF_250M',run_id='saved',rows=[dict(lookback=10,selection='Top 10',rebalance_days=10,history_file='old/location/history.json')])))
            (run/'history.json').write_text(json.dumps([dict(signal_date=self.day.strftime('%Y-%m-%d'),entry_date=self.dates[15].strftime('%Y-%m-%d'),target_weights=json.dumps({'OTHER':1}),tickers='OTHER',provenance={'dataset_fingerprint':'older'})]))
            a=build_audit(self.market,self.strategy,self.day,results_root=root)
            comparison=next(c for c in a['comparisons'] if c['source']=='Strategy Grid')
            self.assertEqual(comparison['status'],'MISMATCH')
            self.assertEqual(comparison['extra_in_candidate'],['OTHER'])
            self.assertFalse(comparison['same_dataset'])
            self.assertEqual(comparison['dataset_fingerprint'],'older')


if __name__=='__main__': unittest.main()

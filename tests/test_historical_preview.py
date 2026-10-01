import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import pandas as pd
from test_regressions import functions
from momentumlab.reconstruction import research_bundle,compare_strategy_targets
from momentumlab.live_model import target_snapshot
from momentumlab.live_service import LiveService
from momentumlab.live_store import LiveStore


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ns=functions()
        days=pd.bdate_range('2026-08-01','2026-09-14')
        raw=pd.DataFrame([dict(ts_code=f'ETF{i}',trade_date=d,close=100+i+j*(i+1),adj_close=100+i+j*(i+1)) for i in range(20) for j,d in enumerate(days)])
        weights=pd.DataFrame([dict(con_code=f'ETF{i}',trade_date=days[0],weight=.05) for i in range(20)])
        ranked=self.ns['merge_membership_and_rank'](self.ns['calculate_signal'](raw,10,calendar=days),weights)
        self.market=dict(raw=raw,adjusted=raw,ranked=ranked,weights=weights,calendar=list(days.strftime('%Y-%m-%d')),
                         data_end='2026-09-14',names={},provenance={'dataset_id':'test'},integrity={'status':'WARNING','warnings':['Partial year']})
        self.strategy=dict(universe='ETF_250M',lookback=10,selection='Top 10',rebalance_days=10,cost_bps=5)
        self.source=dict(run_id='saved-test',**self.strategy,history_files=[])
        self.ns.update(live_market=lambda _:self.market,target_snapshot=target_snapshot,research_bundle=research_bundle,
                       compare_strategy_targets=compare_strategy_targets)
        self.service=LiveService(LiveStore(self.root/'live.sqlite'),lambda _:self.market,self.ns['historical_reconstruction'],self.ns['historical_target_preview'])

    def save_known(self):
        net,_,history,_=self.ns['backtest_v4'](self.market['ranked'],'Top 10',10,pd.Timestamp('2026-09-04'),pd.Timestamp('2026-09-14'),5)
        path=self.root/'saved.json';path.write_text(json.dumps(history.to_dict('records'),default=str))
        self.source['history_files']=[str(path)]
        return path

    def test_weekend_resolves_saved_target_warning_allowed(self):
        self.save_known()
        self.ns['backtest_v4']=Mock(side_effect=AssertionError('Preview must reuse the saved target'))
        for requested in ['2026-09-04','2026-09-05']:
            preview=self.service.preview_historical(dict(start_date=requested,end_date='2026-09-14',start_date_type='SIGNAL'),self.source)
            self.assertEqual(preview['preview_status'],'READY')
            self.assertEqual(preview['signal_date'],'2026-09-04');self.assertEqual(preview['execution_date'],'2026-09-07')
            self.assertEqual(len(preview['holdings']),10)
            self.assertAlmostEqual(sum(r['target_weight'] for r in preview['holdings']),1.)
            self.assertTrue(all(r['score'] is not None for r in preview['holdings']))
            self.assertTrue(preview['integrity']['research_vs_backtest']['match'])
            self.assertEqual(preview['dataset_integrity']['status'],'WARNING')

    def test_missing_saved_uses_existing_engine_first_rebalance_only(self):
        spy=Mock(wraps=self.ns['backtest_v4']);self.ns['backtest_v4']=spy
        preview=self.service.preview_historical(dict(start_date='2026-09-05',end_date='2026-09-14'),self.source)
        self.assertEqual(preview['signal_date'],'2026-09-04');self.assertEqual(len(preview['holdings']),10)
        self.assertEqual(spy.call_args.args[4],pd.Timestamp('2026-09-07'))
        self.assertEqual(preview['integrity']['target_source'],'BACKTEST_GENERATED_FOR_SIGNAL')

    def test_mismatch_blocks_preview_and_creation(self):
        path=self.save_known();records=json.loads(path.read_text())
        records[0]['target_weights']=json.dumps({'WRONG':1.});path.write_text(json.dumps(records))
        params=dict(start_date='2026-09-05',end_date='2026-09-14',start_mode='historical',name='Never created',capital=10000,status='PAPER')
        for action in [self.service.preview_historical,self.service.create]:
            with self.assertRaisesRegex(ValueError,'STRATEGY TARGET MISMATCH'):action(params,self.source)
        self.assertFalse(self.service.store.list_portfolios())

    def test_invalid_date_and_missing_execution_end_reject(self):
        for start,end in [('2026-01-01','2026-09-14'),('2026-09-05','2026-09-06')]:
            with self.assertRaisesRegex(ValueError,'INVALID SIGNAL DATE|NO TARGET FOUND'):
                self.service.preview_historical(dict(start_date=start,end_date=end),self.source)

    def test_create_from_saved_weekend_after_entering_capital(self):
        self.save_known()
        params=dict(start_date='2026-09-05',end_date='2026-09-14',start_mode='historical',name='Saved pilot',capital=10000,status='PAPER')
        self.assertEqual(self.service.preview_historical(params,self.source)['preview_status'],'READY')
        created=self.service.create(params,self.source)
        with self.service.store.transaction() as db:
            portfolio=self.service.store.portfolio(db,created['id'])
            signals=self.service.store.records(db,'signals',created['id'])
        self.assertEqual(portfolio['start_date'],'2026-09-04')
        self.assertEqual(signals[0]['payload']['execution_date'],'2026-09-07')
        self.assertEqual(len(signals[0]['payload']['weights']),10)

if __name__=='__main__':unittest.main()

"""Calendar completeness and signal regressions; synthetic temporary files only."""
import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from test_regressions import functions, TREE
from momentumlab.data_integrity import validate_dataset, require_valid, staged_dataset, validate_calendar
from momentumlab.live_model import target_snapshot, select_target_rows
from momentumlab.research_audit import build_audit


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ns=functions()
        self.days=pd.bdate_range('2024-01-01',periods=35)
        self.calendar=pd.DataFrame({'trade_date':pd.date_range(self.days[0],self.days[-1]),'is_open':0})
        self.calendar.loc[self.calendar.trade_date.isin(self.days),'is_open']=1
        self.raw=pd.DataFrame([dict(ts_code=t,trade_date=d,open=p,high=p,low=p,close=p)
            for k,t in enumerate(['A','B']) for i,d in enumerate(self.days)
            for p in [100*np.exp(.003*i+.01*np.sin(i/(k+2)))]] )
        self.adj=self.raw.rename(columns={c:'adj_'+c for c in ['open','high','low','close']})
        self.fac=self.raw[['ts_code','trade_date']].assign(adj_factor=1.)
        self.weights=pd.DataFrame({'con_code':['A','B'],'trade_date':self.days[12],'weight':.5})
        self.paths={'root':str(self.root)}
        for key,frame in [('raw_price',self.raw),('adjusted_price',self.adj),('adj_factor',self.fac),
                          ('trade_calendar',self.calendar),('weights',self.weights)]:
            self.paths[key+'_csv']=str(self.root/(key+'.csv'));frame.to_csv(self.paths[key+'_csv'],index=False)
        self.life=pd.DataFrame({'ts_code':['A','B'],'list_date':self.days[0],'delist_date':['','']})
        self.life.to_csv(self.root/'securities.csv',index=False)

    def report(self,**kw):return validate_dataset(self.paths,universe_id='CSI300',lookback=10,**kw)

    def drop(self,key,ticker,indices):
        frame=pd.read_csv(self.paths[key+'_csv'])
        dates=pd.to_datetime(frame.trade_date)
        frame=frame[~(frame.ts_code.eq(ticker)&dates.isin(self.days[indices]))]
        frame.to_csv(self.paths[key+'_csv'],index=False)

    def ranked(self):
        prices=pd.read_csv(self.paths['adjusted_price_csv'])
        return self.ns['merge_membership_and_rank'](self.ns['calculate_signal'](prices,10,self.calendar),self.weights)

    def test_middle_missing_in_both_and_read_only(self):
        self.drop('raw_price','A',[16]);self.drop('adjusted_price','A',[16])
        before={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for k,p in self.paths.items() if k!='root'}
        r=self.report()
        self.assertNotEqual(r['structural_status'],'FAIL')
        self.assertEqual(r['completeness']['status'],'FAIL')
        missing=[x for x in r['completeness']['findings'] if x['severity']=='FAIL']
        self.assertEqual({x['affected_component'] for x in missing},{'raw_price','adjusted_price'})
        self.assertEqual({x['date'] for x in missing},{self.days[16].strftime('%Y%m%d')})
        self.assertEqual(before,{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before})
        json.dumps(r,allow_nan=False)

    def test_stale_ticker_while_peers_current(self):
        for key in ['raw_price','adjusted_price','adj_factor']:self.drop(key,'A',[33,34])
        r=self.report()['completeness']
        self.assertEqual(r['status'],'WARNING')
        self.assertFalse(any(x['ticker']=='A' for x in r['findings']))

    def test_listing_and_delisting_are_explained_but_short_window_excluded(self):
        self.life.loc[self.life.ts_code.eq('A'),'list_date']=self.days[15]
        self.life.loc[self.life.ts_code.eq('B'),'delist_date']=self.days[31].strftime('%Y-%m-%d')
        self.life.to_csv(self.root/'securities.csv',index=False)
        for key in ['raw_price','adjusted_price','adj_factor']:
            self.drop(key,'A',list(range(15)));self.drop(key,'B',[32,33,34])
        report=self.report()['completeness']
        self.assertNotIn('BEFORE_LISTING',report['counts']);self.assertNotIn('AFTER_DELISTING',report['counts'])
        ranked=self.ranked()
        self.assertTrue(ranked[(ranked.ts_code=='A')&(ranked.trade_date==self.days[20])].momentum_score.isna().all())
        self.assertTrue(ranked[(ranked.ts_code=='A')&(ranked.trade_date==self.days[25])].momentum_score.notna().all())

    def test_suspension_explains_gap_never_bridges_signal(self):
        for key in ['raw_price','adjusted_price','adj_factor']:self.drop(key,'A',[16])
        pd.DataFrame([dict(ts_code='A',start_date=self.days[16],end_date=self.days[16],full_session=1,source='exchange notice fixture')]).to_csv(self.root/'suspensions.csv',index=False)
        r=self.report()['completeness']
        self.assertEqual(r['status'],'PASS');self.assertEqual(r['counts']['CONFIRMED_SUSPENSION'],3)
        ranked=self.ranked();a=ranked[ranked.ts_code=='A'].set_index('trade_date')
        self.assertTrue(a.loc[self.days[17:27],'momentum_score'].isna().all())
        self.assertTrue(pd.notna(a.loc[self.days[27],'momentum_score']))

    def test_missing_factor_has_ticker_date(self):
        self.drop('adj_factor','A',[16]);r=self.report()
        self.assertEqual(r['status'],'FAIL')
        self.assertTrue(any(x['ticker']=='A' and x['affected_component']=='adjustment_factor' and
                            x['date']==self.days[16].strftime('%Y%m%d') for x in r['completeness']['findings']))

    def test_pre_eligibility_warmup_required(self):
        self.assertTrue(self.ranked().query('trade_date == @self.days[12]').momentum_score.notna().all())
        self.drop('raw_price','A',[7]);self.drop('adjusted_price','A',[7])
        self.assertEqual(self.report()['completeness']['status'],'FAIL')
        row=self.ranked().query('ts_code == "A" and trade_date == @self.days[12]').iloc[0]
        self.assertTrue(pd.isna(row.momentum_rank))

    def test_complete_data_scores_targets_and_backtest_unchanged(self):
        original=copy.deepcopy(next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='calculate_signal'))
        original.name='original_signal';original.args.args=original.args.args[:2];original.args.defaults=[]
        original.body=original.body[1:-1]+[ast.Return(value=ast.Name(id='x',ctx=ast.Load()))]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[original],type_ignores=[])),'fixture','exec'),self.ns)
        expected=self.ns['merge_membership_and_rank'](self.ns['original_signal'](self.adj,10),self.weights)
        actual=self.ranked()
        # CSV decimal parsing may differ at machine precision; use identical input.
        source=pd.read_csv(self.paths['adjusted_price_csv'])
        expected=self.ns['merge_membership_and_rank'](self.ns['original_signal'](source,10),self.weights)
        assert_frame_equal(expected,actual[expected.columns],check_exact=True)
        old=self.ns['backtest_v4'](expected,'Top 10',10)
        new=self.ns['backtest_v4'](actual,'Top 10',10)
        pd.testing.assert_series_equal(old[0],new[0],check_exact=True)
        assert_frame_equal(old[2],new[2],check_exact=True)

    def test_calendar_required_and_entire_missing_day_detected(self):
        with self.assertRaisesRegex(ValueError,'calendar required'):self.ns['calculate_signal'](self.adj,10)
        for t in ['A','B']:
            for k in ['raw_price','adjusted_price','adj_factor']:self.drop(k,t,[16])
        self.assertEqual(self.report()['completeness']['status'],'FAIL')
        ranked=self.ranked()
        self.assertTrue(ranked[ranked.trade_date.eq(self.days[20])].momentum_score.isna().all())
        market=dict(ranked=ranked,calendar=list(self.days.strftime('%Y-%m-%d')),data_end=str(self.days[-1].date()))
        with self.assertRaisesRegex(ValueError,'exact signal date'):target_snapshot(dict(selection='Top 10'),market,str(self.days[20].date()))

    def test_current_latest_does_not_conceal_history_or_publish(self):
        for key in ['raw_price','adjusted_price']:self.drop(key,'A',[16])
        calls=Mock(side_effect=AssertionError('no download expected'))
        self.ns.update(read_csv=lambda p:pd.read_csv(p),tushare_call=calls)
        missing=self.ns['_incremental_open_sessions'](self.paths['trade_calendar_csv'],self.paths['raw_price_csv'],self.days[-1])
        self.assertEqual(missing,[])
        result=self.ns['fetch_etf_date_batches']('fund_daily',['A','B'],missing,self.paths['raw_price_csv'],'',['ts_code','trade_date'],'raw')
        self.assertIn('SKIPPED',result['status'])
        with staged_dataset(self.paths) as work:
            report=validate_dataset(work,universe_id='CSI300',lookback=10)
            with self.assertRaisesRegex(ValueError,'completeness'):require_valid(report)
        calls.assert_not_called()

    def test_audit_live_research_grid_share_exclusions(self):
        self.drop('adjusted_price','A',[16]);ranked=self.ranked()
        market=dict(ranked=ranked,calendar=list(self.days.strftime('%Y-%m-%d')),weights=self.weights,
                    names={},data_end=str(self.days[-1].date()))
        strategy=dict(universe='CSI300',lookback=10,selection='Top 10',rebalance_days=10)
        day=self.days[22]
        target=target_snapshot(strategy,market,str(day.date()))
        audit=build_audit(market,strategy,day)
        self.assertEqual(set(target['weights']),{'B'})
        self.assertEqual([r['ticker'] for r in audit['top10_target']],['B'])
        self.assertIn('A',[r['ticker'] for r in audit['excluded_securities']])
        for _ in ['research','grid']:
            _,_,history,_=self.ns['backtest_v4'](ranked,'Top 10',10,day,self.days[-1])
            self.assertEqual(history.iloc[0].tickers,'B')

    def test_missing_lifecycle_is_warning_and_cache_tracks_evidence(self):
        (self.root/'securities.csv').unlink()
        r=self.report();self.assertEqual(r['completeness']['status'],'WARNING')
        self.assertNotIn('LIFECYCLE_UNAVAILABLE',r['completeness']['counts'])
        self.life.to_csv(self.root/'securities.csv',index=False)
        changed=self.report();self.assertNotEqual(r['manifest']['dataset_fingerprint'],changed['manifest']['dataset_fingerprint'])
        self.assertNotIn('LIFECYCLE_UNAVAILABLE',changed['completeness']['counts'])

    def test_reversed_lifecycle_cannot_explain_missing_history(self):
        pd.DataFrame([dict(ts_code='A',list_date='20260101',delist_date='20200101')]).to_csv(self.root/'securities.csv',index=False)
        result=self.report()['completeness']
        self.assertTrue(any('unusable lifecycle evidence' in item for item in result['limitations']))


if __name__=='__main__':unittest.main()

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
import pandas as pd
from test_regressions import functions
from momentumlab.live_store import LiveStore
from momentumlab.live_service import LiveService
from momentumlab.live_export import export_portfolio
from momentumlab.reconstruction import research_bundle


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'live.sqlite3'
        self.store=LiveStore(self.path)
        dates=pd.bdate_range('2024-01-01',periods=45)
        self.market={'raw':pd.DataFrame([dict(ts_code=str(i),trade_date=d,close=10+i,adj_close=10+i) for i in range(10) for d in dates]),
                     'calendar':list(dates.strftime('%Y-%m-%d')), 'data_end':dates[-1].strftime('%Y-%m-%d'),'names':{}}
        self.market['adjusted']=self.market['raw'].copy()
        self.market['ranked']=pd.DataFrame([dict(ts_code=str(i),trade_date=d,momentum_rank=i+1,quintile=5-i//2,csi300_member=1) for i in range(10) for d in dates])
        ranked=self.market['raw'].merge(self.market['ranked'],on=['ts_code','trade_date'])
        ns=functions()
        def reconstruct(strategy,start,end,source):
            net,_,history,_=ns['backtest_v4'](ranked,strategy['selection'],strategy['rebalance_days'],pd.Timestamp(start),pd.Timestamp(end),strategy['cost_bps'])
            return research_bundle(history,net,source['run_id'])
        self.service=LiveService(self.store,lambda s:self.market,reconstruct)
        self.source=dict(run_id='test-run',universe='CSI300',lookback=10,selection='Top 5',rebalance_days=5,cost_bps=5)
        self.pid=self.service.create(dict(name='Pilot',start_date='2024-01-02',capital=10000,status='ACTIVE'),self.source)['id']

    def trade(self, **kw):
        p=dict(ticker='0',side='BUY',date='2024-01-03',price=10,quantity=100,fee=2,notes='manual')
        p.update(kw)
        return self.service.record_trade(self.pid,p)

    def test_integrity_failure_preserves_active_holdings_and_signals(self):
        self.trade()
        with self.store.transaction() as db:
            before={table:self.store.records(db,table,self.pid) for table in ['trades','signals','rebalances','events']}
        ns=functions()
        ns.update(load_config=lambda:{},selected_universe_paths=lambda *args:{},
                  validate_research_files=lambda *args:{'ok':False,'errors':['conflicting duplicate prices']})
        self.service.market_provider=ns['live_market']
        with self.assertRaisesRegex(ValueError,'Actual portfolio state unchanged'):
            self.service.detail(self.pid,refresh=True)
        with self.store.transaction() as db:
            after={table:self.store.records(db,table,self.pid) for table in before}
        self.assertEqual(before,after)

    def test_nav_cash_fees_and_model_targets(self):
        self.trade()
        d=self.service.detail(self.pid,refresh=True)
        self.assertAlmostEqual(d['stats']['cash'],8998)
        self.assertAlmostEqual(d['stats']['current_nav'],9998)
        self.assertAlmostEqual(d['stats']['current_nav'],d['stats']['cash']+sum(r['market_value'] for r in d['positions']))
        self.assertEqual(len(d['signals'][0]['payload']['weights']),5)
        self.assertLess(d['stats']['model_return'],0)
        self.assertTrue(d['signals'][0]['payload']['backfilled'])
        self.assertGreater(len(d['signals']),1)
        json.dumps(d,allow_nan=False)

    def test_partial_sell_dividend_and_split(self):
        self.trade()
        self.trade(side='SELL',date='2024-01-04',quantity=40,price=12,fee=1)
        self.trade(side='DIVIDEND',date='2024-01-05',quantity=0,price=30,fee=0)
        self.trade(side='SPLIT',date='2024-01-08',quantity=2,price=0,fee=0)
        d=self.service.detail(self.pid)
        p=next(p for p in d['positions'] if p['ticker']=='0')
        self.assertEqual(p['quantity'],120)
        self.assertAlmostEqual(p['average_cost'],5.01)
        self.assertAlmostEqual(d['stats']['cash'],9507)

    def test_invalid_sell_and_cash_are_atomic(self):
        with self.assertRaises(ValueError):self.trade(side='SELL')
        with self.assertRaises(ValueError):self.trade(quantity=2000)
        self.assertEqual(len(self.service.detail(self.pid)['trades']),0)

    def test_corrections_preserve_versions_and_restart(self):
        original=self.trade()['id']
        self.trade(supersedes=original,quantity=50)
        d=LiveService(LiveStore(self.path),lambda s:self.market).detail(self.pid)
        self.assertEqual(len(d['trades']),1)
        self.assertEqual(len(d['trade_versions']),2)
        self.assertEqual(d['trades'][0]['quantity'],50)
        self.assertIn('Trade Edited',[e['event_type'] for e in d['events']])
        with self.assertRaises(ValueError):self.trade(supersedes=original)

    def test_retry_and_journal_are_not_duplicated(self):
        self.trade(request_id='one');self.trade(request_id='one')
        d=self.service.detail(self.pid);signal=d['signals'][0]['id']
        journal=self.service.journal(self.pid,dict(date='2024-01-04',signal_id=signal,notes='done'))
        self.assertAlmostEqual(journal['actual_turnover'],.5*(1000/9998))
        self.assertEqual(journal['fees_paid'],2)
        with self.assertRaises(ValueError):self.service.journal(self.pid,dict(date='2024-01-04',signal_id=signal))

    def test_paper_cannot_become_actual_after_pause(self):
        pid=self.service.create(dict(name='Paper',start_date='2024-01-02',capital=1000,status='PAPER'),self.source)['id']
        self.service.set_status(pid,'PAUSED')
        with self.assertRaises(ValueError):self.service.set_status(pid,'ACTIVE')
        self.service.set_status(pid,'PAPER')
        self.assertTrue(self.service.detail(pid)['is_paper'])

    def test_paused_targets_do_not_regenerate_and_close_blocks_trades(self):
        self.service.set_status(self.pid,'PAUSED')
        d=self.service.detail(self.pid,refresh=True)
        self.assertEqual(len(d['signals']),1)
        self.service.set_status(self.pid,'CLOSED')
        with self.assertRaises(ValueError):self.trade()

    def test_exports_and_audit(self):
        self.trade(notes='=WEBSERVICE("bad")')
        d=self.service.detail(self.pid,refresh=True)
        files=export_portfolio(d,self.tmp.name)
        self.assertEqual(set(files),{'portfolio_summary.csv','live_positions.csv','live_trades.csv','rebalance_history.csv','live_nav.csv','audit.json'})
        with open(files['live_trades.csv'],encoding='utf-8-sig') as f:
            self.assertTrue(next(csv.DictReader(f))['notes'].startswith("'="))
        with self.store.transaction() as db:
            self.assertGreater(db.execute('SELECT count(*) FROM nav_history').fetchone()[0],0)

    def test_next_execution_on_signal_day_and_pending_calendar(self):
        from momentumlab.live_model import next_schedule
        from momentumlab.live_valuation import build_nav
        schedule=next_schedule('2024-01-02',self.market['calendar'],5,'2024-01-02')
        self.assertEqual(schedule['next_rebalance'],'2024-01-03')
        detail=self.service.detail(self.pid)
        signals=detail['signals']
        signals[0]['payload']['execution_date']=None
        nav,_=build_nav(detail['portfolio'],[],signals,self.market,'2024-01-04')
        self.assertLess(nav[-1]['model_nav'],10000)
        missing=dict(self.market)
        missing['adjusted']=self.market['adjusted'][self.market['adjusted'].trade_date!=pd.Timestamp('2024-01-03')]
        nav,_=build_nav(detail['portfolio'],[],signals,missing,'2024-01-04')
        self.assertEqual(nav[-1]['model_nav'],10000)
        self.assertTrue(nav[1]['model_unfilled'])

    def test_retry_rejects_changed_payload(self):
        self.trade(request_id='same')
        with self.assertRaises(ValueError):self.trade(request_id='same',quantity=50)

    def test_historical_paper_reconciles_and_tracks_automatically(self):
        end=self.market['calendar'][-1]
        pid=self.service.create(dict(name='Historical Trial',start_date='2024-01-02',end_date=end,start_mode='historical',capital=10000,status='PAPER'),self.source)['id']
        detail=self.service.detail(pid,refresh=True)
        self.assertTrue(detail['automatic_paper'])
        self.assertEqual(detail['reconciliation']['status'],'MATCH')
        self.assertEqual(len(detail['positions']),5)
        self.assertGreater(len(detail['model_journals']),1)
        self.assertTrue(all(r['source']=='PAPER' for r in detail['position_history']))
        with self.assertRaises(ValueError):self.service.record_trade(pid,dict(ticker='0',side='BUY',date='2024-01-03',price=10,quantity=1,fee=0))

    def test_trial_delete_archive_and_separate_active_promotion(self):
        trial=self.service.create(dict(name='Trial',start_date='2024-01-02',capital=1000,status='PAPER'),self.source)['id']
        deleted=self.service.create(dict(name='Delete Me',start_date='2024-01-02',capital=1000,status='PAPER'),self.source)['id']
        self.service.delete_trial(deleted)
        self.assertNotIn(deleted,[p['id'] for p in self.store.list_portfolios(True)])
        active=self.service.promote(trial,dict(name='Actual',activation_date=date.today().isoformat(),capital=2000))['id']
        paper=self.service.detail(trial);actual=self.service.detail(active)
        self.assertEqual(paper['portfolio']['mode'],'PAPER')
        self.assertEqual(actual['portfolio']['mode'],'ACTIVE')
        self.assertEqual(actual['portfolio']['parent_id'],trial)
        self.assertEqual(actual['portfolio']['strategy']['signal_anchor'],paper['portfolio']['strategy']['signal_anchor'])
        self.assertAlmostEqual(actual['stats']['current_nav'],paper['stats']['current_nav'])
        self.assertEqual({r['ticker']:r['quantity'] for r in actual['positions'] if r['quantity']>0},
                         {r['ticker']:r['quantity'] for r in paper['positions'] if r['quantity']>0})
        self.assertTrue(all(t['side']=='OPENING_TRANSFER' for t in actual['trades']))
        self.assertEqual(actual['rebalance_workflow']['last_rebalance_date'],paper['rebalance_workflow']['last_rebalance_date'])
        with self.assertRaises(ValueError):self.service.delete_trial(trial)
        self.service.archive(active)
        self.assertNotIn(active,[p['id'] for p in self.store.list_portfolios()])

    def test_active_rebalance_is_manual_partial_and_advances_only_when_finalized(self):
        before=self.service.detail(self.pid,refresh=True)
        self.assertEqual(before['stats']['cash'],10000)
        self.assertEqual(before['rebalance_workflow']['status'],'WAITING_FOR_EXECUTION')
        self.assertTrue(before['rebalance_workflow']['initial_funding'])
        signal=before['rebalance_workflow']['signal_id'];target=next(s for s in before['signals'] if s['id']==signal)['payload']['weights']
        first=next(r for r in before['rebalance_workflow']['recommendations'] if r['action']=='NEW BUY')
        result=self.service.record_actual_rebalance(self.pid,dict(signal_id=signal,trades=[dict(ticker=first['ticker'],side='BUY',date='2024-01-03',price=10+int(first['ticker']),quantity=10,fee=1)],mark_completed=False))
        self.assertEqual(result['status'],'PARTIALLY_EXECUTED')
        partial=self.service.detail(self.pid)
        self.assertEqual(partial['rebalance_workflow']['signal_id'],signal)
        self.assertEqual(partial['rebalance_workflow']['status'],'PARTIALLY_EXECUTED')
        self.assertEqual(next(s for s in partial['signals'] if s['id']==signal)['payload']['weights'],target)
        second=next(r for r in partial['rebalance_workflow']['recommendations'] if r['ticker']==first['ticker'])
        self.service.record_actual_rebalance(self.pid,dict(signal_id=signal,trades=[dict(ticker=second['ticker'],side='BUY',date='2024-01-04',price=10+int(second['ticker']),quantity=1,fee=0)],mark_completed=True))
        after=self.service.detail(self.pid)
        self.assertNotEqual(after['rebalance_workflow']['signal_id'],signal)
        self.assertEqual(len(after['journals']),2)
        self.assertEqual(after['journals'][0]['payload']['status'],'PARTIALLY_EXECUTED')
        self.assertEqual(after['journals'][1]['payload']['status'],'COMPLETED')
        self.assertEqual(after['stats']['number_of_rebalances'],0)  # initial funding is separate

    def test_paper_apply_persists_history_and_advances_in_order(self):
        pid=self.service.create(dict(name='Paper Apply',start_date='2024-01-02',capital=10000,status='PAPER'),self.source)['id']
        first=self.service.detail(pid,refresh=True);signal1=first['rebalance_workflow']['signal_id']
        self.assertEqual(first['rebalance_workflow']['status'],'WAITING_FOR_EXECUTION')
        self.service.apply_paper_rebalance(pid,dict(signal_id=signal1,notes='first recurring'))
        second=self.service.detail(pid);signal2=second['rebalance_workflow']['signal_id']
        self.assertNotEqual(signal1,signal2)
        self.service.apply_paper_rebalance(pid,dict(signal_id=signal2,capital_adjustment='ADD',capital_amount=1000,notes='second recurring'))
        third=self.service.detail(pid)
        self.assertEqual([j['payload']['status'] for j in third['journals']],['COMPLETED','COMPLETED'])
        self.assertEqual(third['stats']['number_of_rebalances'],2)
        expected=next(s for s in third['signals'] if s['id']==signal2)['payload']['weights']
        held={r['ticker'] for r in third['positions'] if r['quantity']>0}
        self.assertEqual(held,set(expected))
        self.assertEqual(third['stats']['external_contributions'],1000)
        self.assertEqual(third['journals'][-1]['payload']['contribution'],1000)
        self.assertLess(abs(third['stats']['cumulative_return']),.01)

    def test_schedule_counts_exchange_sessions_across_weekend_and_holiday(self):
        from momentumlab.live_model import scheduled_signals,next_schedule
        calendar=['2024-01-02','2024-01-03','2024-01-04','2024-01-05','2024-01-08','2024-01-09','2024-01-10','2024-01-11']
        self.assertEqual(scheduled_signals('2024-01-05',calendar,3,'2024-01-10'),['2024-01-05','2024-01-10'])
        self.assertEqual(next_schedule('2024-01-05',calendar,3,'2024-01-05')['next_rebalance'],'2024-01-08')
        self.assertEqual(next_schedule('2024-01-05',calendar,3,'2024-01-08'),{'next_signal':'2024-01-10','next_rebalance':'2024-01-11','calendar_end':'2024-01-11'})
        ten_day_calendar=['2024-02-09','2024-02-19','2024-02-20','2024-02-21','2024-02-22','2024-02-23','2024-02-26','2024-02-27','2024-02-28','2024-02-29','2024-03-01','2024-03-04']
        self.assertEqual(scheduled_signals('2024-02-09',ten_day_calendar,10,'2024-03-01'),['2024-02-09','2024-03-01'])
        self.assertEqual(next_schedule('2024-02-09',ten_day_calendar,10,'2024-02-19')['next_rebalance'],'2024-03-04')

    def test_external_capital_is_excluded_from_return_and_funds_compounding_target(self):
        detail=self.service.detail(self.pid);signal=detail['rebalance_workflow']['signal_id']
        self.service.record_actual_rebalance(self.pid,dict(signal_id=signal,trades=[],capital_adjustment='ADD',capital_amount=2000,mark_completed=False))
        added=self.service.detail(self.pid)
        self.assertEqual(added['stats']['current_nav'],12000)
        self.assertEqual(added['stats']['external_contributions'],2000)
        self.assertAlmostEqual(added['stats']['investment_profit_loss'],0)
        self.assertAlmostEqual(added['stats']['cumulative_return'],0)
        preview=self.service.preview_rebalance(self.pid,dict(capital_adjustment='NONE',capital_amount=0))
        self.assertEqual(preview['investable_nav'],12000)
        self.assertAlmostEqual(sum(r['target_value'] for r in preview['recommendations']),12000)

        second=self.service.create(dict(name='Withdrawal',start_date='2024-01-02',capital=10000,status='ACTIVE'),self.source)['id']
        due=self.service.detail(second)['rebalance_workflow']['signal_id']
        self.service.record_actual_rebalance(second,dict(signal_id=due,trades=[],capital_adjustment='WITHDRAW',capital_amount=1000,mark_completed=False))
        withdrawn=self.service.detail(second)
        self.assertEqual(withdrawn['stats']['current_nav'],9000)
        self.assertEqual(withdrawn['stats']['external_withdrawals'],1000)
        self.assertAlmostEqual(withdrawn['stats']['cumulative_return'],0)

    def test_proposal_classifies_new_carried_and_exit_rows(self):
        from momentumlab.rebalance_workflow import recommendations
        positions=[dict(ticker='A',quantity=10,current_price=10,market_value=100),
                   dict(ticker='B',quantity=10,current_price=10,market_value=100),
                   dict(ticker='D',quantity=10,current_price=10,market_value=100)]
        rows,_=recommendations(positions,{'A':.4,'B':.2,'C':.4},{},500,{'A':1,'B':2,'C':3})
        actions={r['ticker']:(r['action'],r['status']) for r in rows}
        self.assertEqual(actions['A'],('CARRY / INCREASE','CARRIED'))
        self.assertEqual(actions['B'],('CARRY / NO MATERIAL CHANGE','CARRIED'))
        self.assertEqual(actions['C'],('NEW BUY','NEW'))
        self.assertEqual(actions['D'],('SELL OUT','EXIT'))

    def test_paper_gains_withdrawal_and_same_day_catch_up_reconcile_preview(self):
        pid=self.service.create(dict(name='Compounding',start_date='2024-01-02',capital=10000,status='PAPER'),self.source)['id']
        # Double prices after initial deployment: the next allocation must use gains.
        for key in ('raw','adjusted'):
            later=self.market[key]['trade_date']>pd.Timestamp('2024-01-03')
            self.market[key].loc[later,['close','adj_close']]*=2
        before=self.service.detail(pid)
        self.assertGreater(before['stats']['current_nav'],19000)
        for kind,amount in [('WITHDRAW',12000),('ADD',2000)]:
            before=self.service.detail(pid)
            proposal=self.service.preview_rebalance(pid,dict(capital_adjustment=kind,capital_amount=amount))
            self.assertAlmostEqual(proposal['expected_post_rebalance_cash'],0)
            self.service.apply_paper_rebalance(pid,dict(signal_id=before['rebalance_workflow']['signal_id'],capital_adjustment=kind,capital_amount=amount))
            after=self.service.detail(pid,refresh=True)
            expected=proposal['investable_nav']-proposal['estimated_trading_cost']
            self.assertAlmostEqual(after['stats']['current_nav'],expected)
            self.assertAlmostEqual(after['journals'][-1]['payload']['nav_after'],expected)
            self.assertAlmostEqual(after['stats']['current_nav'],after['stats']['cash']+sum(r['market_value'] for r in after['positions']))
            snapshot=next(e for e in reversed(after['events']) if e['event_type']=='NAV Snapshot')
            self.assertAlmostEqual(snapshot['payload']['nav'],expected)
        self.assertEqual(after['stats']['external_withdrawals'],12000)
        self.assertEqual(after['stats']['external_contributions'],2000)
        self.assertGreater(after['stats']['cumulative_return'],.9)

    def test_execution_date_historical_start_maps_to_prior_signal(self):
        preview=self.service.preview_historical(dict(start_date='2024-01-03',start_date_type='EXECUTION',end_date='2024-01-10'),self.source)
        self.assertEqual(preview['requested_start_date'],'2024-01-03')
        self.assertEqual(preview['signal_date'],'2024-01-02')
        self.assertEqual(preview['execution_date'],'2024-01-03')

    def test_close_archive_restore_and_scoped_active_delete(self):
        keep=self.service.create(dict(name='Keep',start_date='2024-01-02',capital=1000,status='ACTIVE'),self.source)['id']
        doomed=self.service.create(dict(name='Doomed',start_date='2024-01-02',capital=1000,status='ACTIVE'),self.source)['id']
        self.service.set_status(keep,'CLOSED')
        self.assertIn(keep,[p['id'] for p in self.store.list_portfolios('closed')])
        self.service.archive(keep)
        self.assertIn(keep,[p['id'] for p in self.store.list_portfolios('archived')])
        self.service.restore(keep)
        self.assertIn(keep,[p['id'] for p in self.store.list_portfolios('closed')])
        with self.assertRaises(ValueError): self.service.delete_portfolio(doomed,'delete')
        self.service.delete_portfolio(doomed,'DELETE')
        all_ids=[p['id'] for p in self.store.list_portfolios('all')]
        self.assertNotIn(doomed,all_ids);self.assertIn(keep,all_ids);self.assertIn(self.pid,all_ids)

    def test_close_preserves_holdings_and_clears_pending_workflow(self):
        self.trade()
        before=self.service.detail(self.pid)
        self.service.set_status(self.pid,'CLOSED')
        after=self.service.detail(self.pid,refresh=True)
        self.assertEqual(before['trades'],after['trades'])
        self.assertAlmostEqual(before['stats']['current_nav'],after['stats']['current_nav'])
        self.assertIsNone(after['rebalance_workflow']['signal_id'])
        self.assertIsNone(after['schedule']['next_signal'])

    def test_promotion_rejects_incompatible_model_units_without_writing(self):
        pid=self.service.create(dict(name='Different units',start_date='2024-01-02',capital=10000,status='PAPER'),self.source)['id']
        self.market['adjusted']['adj_close']*=2
        count=len(self.store.list_portfolios('all'))
        with self.assertRaisesRegex(ValueError,'model-unit quantities'):
            self.service.promote(pid,dict(activation_date=date.today().isoformat()))
        self.assertEqual(len(self.store.list_portfolios('all')),count)
        self.assertEqual(self.service.detail(pid)['portfolio']['status'],'PAPER')


if __name__=='__main__':unittest.main()

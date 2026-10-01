"""Portable resolver and live calendar checks without importing chart binaries."""
import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from test_regressions import functions, TREE
from momentumlab.live_model import next_schedule
from momentumlab.live_service import LiveService
from momentumlab.live_store import LiveStore


class PortableCalendarTests(unittest.TestCase):
    def test_calendar_download_requests_future_sessions_without_extending_price_end(self):
        ns=functions(); calls=[]; validated=[]
        response=pd.DataFrame({'cal_date':['20990102','20990601'],'is_open':[1,1]})
        ns.update(tushare_call=lambda api,params,fields:(calls.append((api,params)) or response),
                  append_dedupe=lambda *args:response,read_csv=lambda path:response,
                  validate_calendar=lambda frame,end:validated.append(end))
        ns['fetch_trade_calendar']('2099-01-01','2099-01-31','unused.csv')
        self.assertEqual(calls[0][0],'trade_cal')
        self.assertEqual(calls[0][1]['end_date'],'20990531')
        self.assertEqual(validated,['2099-01-31'])

    def test_move_source_and_frozen_with_old_copy_still_present(self):
        with tempfile.TemporaryDirectory() as folder:
            old=Path(folder)/'original'/'MomentumLab';new=Path(folder)/'copied'/'MomentumLab'
            data=old/'data'/'ETF_250M';data.mkdir(parents=True)
            (old/'config').mkdir();(old/'results').mkdir()
            dates=pd.bdate_range('2026-07-01','2026-09-11')
            prices=pd.DataFrame([dict(ts_code=f'ETF{i}',trade_date=d,close=10+i+j*.01*(i+1),adj_close=10+i+j*.01*(i+1))
                                 for i in range(25) for j,d in enumerate(dates)])
            for c in ['open','high','low','adj_open','adj_high','adj_low']: prices[c]=prices.close
            prices.to_csv(data/'prices.csv',index=False);prices.to_csv(data/'adjusted_prices.csv',index=False)
            prices[['ts_code','trade_date']].assign(adj_factor=1).to_csv(data/'adj_factors.csv',index=False)
            pd.DataFrame([dict(ts_code=f'ETF{i}',name=f'Equity {i}',fund_type='Equity') for i in range(25)]).to_csv(data/'fund_classification.csv',index=False)
            pd.DataFrame([dict(con_code=f'ETF{i}',trade_date=dates[0],weight=.04,market_cap_rmb=300000000) for i in range(25)]).to_csv(data/'eligibility.csv',index=False)
            calendar=pd.bdate_range('2026-07-01','2026-12-31')
            pd.DataFrame(dict(trade_date=calendar,is_open=1)).to_csv(data/'calendar.csv',index=False)
            settings=dict(data_folder=str(old/'data'),results_folder=str(old/'results'),active_universe='ETF_250M',
                          universe_file_paths={'ETF_250M':{'trade_calendar_csv':str(data/'calendar.csv')}})
            settings.update(trade_calendar_csv=str(data/'calendar.csv'))
            (old/'config'/'settings.json').write_text(json.dumps(settings),encoding='utf-8')
            shutil.copytree(old,new)
            original_cwd=Path.cwd()
            try:
                os.chdir(folder)
                for frozen in (False,True):
                    (new/'config'/'settings.json').write_text(json.dumps(settings),encoding='utf-8')
                    ns=functions()
                    ns.update(__file__=str(new/'MomentumLabV2.py'),sys=SimpleNamespace(frozen=frozen,executable=str(new/'MomentumLabV2.exe'),_MEIPASS=str(Path(folder)/'bundle')))
                    root=ns['app_dir']()
                    self.assertEqual(root,new)
                    self.assertEqual(ns['resource_dir'](),Path(folder)/'bundle' if frozen else new)
                    ns.update(ROOT=root,CONFIG_DIR=root/'config',CONFIG_FILE=root/'config'/'settings.json',LOG_DIR=root/'logs',_LIVE_MARKET_CACHE={})
                    nodes=[n for n in TREE.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'DEFAULTS','UNIVERSES','FILE_KEYS'} for t in n.targets)]
                    exec(compile(ast.Module(body=nodes,type_ignores=[]),'constants','exec'),ns)
                    # The fixture is already an eligible equity universe; no classification download.
                    ns['filter_equity_etf_weights']=lambda w:w
                    cfg=ns['load_config']();paths=ns['selected_universe_paths'](cfg,'ETF_250M')
                    self.assertEqual(Path(paths['trade_calendar_csv']),new/'data'/'ETF_250M'/'calendar.csv')
                    self.assertTrue(ns['data_status']()['calendar']['exists'])
                    self.assertTrue(ns['data_status']()['raw']['exists'])
                    self.assertTrue(ns['data_status']()['adjusted']['exists'])
                    self.assertTrue(ns['validate_research_files']('ETF_250M')['ok'])
                    strategy=dict(universe='ETF_250M',lookback=10,selection='Top 10',rebalance_days=10,cost_bps=5)
                    market=ns['live_market'](strategy)
                    self.assertEqual(market['data_end'],'2026-09-11')
                    self.assertEqual(market['calendar'][-1],'2026-12-31')
                    schedule=next_schedule('2026-09-04',market['calendar'],10,'2026-09-11')
                    self.assertEqual(schedule['next_signal'],'2026-09-18')
                    self.assertEqual(schedule['next_rebalance'],'2026-09-21')
                    grid=ns['run_strategy_grid'](dict(universe='ETF_250M',lookbacks=[10],rebalances=[10],selections=['Top 10'],years=[2026],include_full=True,trading_cost_bps=5))
                    self.assertEqual(grid['failed_combinations'],0)
                    service=LiveService(LiveStore(new/f'live-{frozen}.sqlite3'),lambda _:market)
                    pid=service.create(dict(name='Portable',start_date='2026-09-04',capital=10000,status='PAPER'),dict(run_id='fixture',**strategy))['id']
                    detail=service.detail(pid)
                    self.assertEqual(detail['rebalance_workflow']['signal_date'],'2026-09-18')
                    self.assertEqual(detail['rebalance_workflow']['execution_date'],'2026-09-21')
                    self.assertIsInstance(detail['rebalance_workflow']['trading_sessions_until'],int)
                    self.assertIsInstance(detail['rebalance_workflow']['trading_sessions_until_signal'],int)
                    self.assertIsNone(detail['calendar_diagnostics']['failure_reason'])
                    self.assertEqual(detail['rebalance_workflow']['target_status'],'NOT YET GENERATED')
                    self.assertFalse(any(s['signal_date']=='2026-09-18' for s in detail['signals']))
                    self.assertEqual(detail['calendar_diagnostics']['calendar_file'],'data/ETF_250M/calendar.csv')
                    ns['save_config'](cfg)
                    saved=json.loads((new/'config'/'settings.json').read_text(encoding='utf-8'))
                    self.assertEqual(saved['data_folder'],'data')
                    self.assertEqual(saved['trade_calendar_csv'],'data/ETF_250M/calendar.csv')
            finally: os.chdir(original_cwd)

    def test_known_signal_retained_when_only_execution_is_beyond_calendar(self):
        calendar=list(pd.bdate_range('2026-09-04','2026-09-18').strftime('%Y-%m-%d'))
        result=next_schedule('2026-09-04',calendar,10,'2026-09-11')
        self.assertEqual(result['next_signal'],'2026-09-18')
        self.assertIsNone(result['next_rebalance'])

    def test_missing_calendar_has_specific_diagnostics(self):
        ns=functions()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ns['ROOT']=root
            calendar,diagnostics=ns['live_calendar']({'data_folder':str(root/'data')},
                {'root':str(root/'data'/'ETF_250M'),'trade_calendar_csv':str(root/'data'/'ETF_250M'/'calendar.csv')},'ETF_250M')
            self.assertEqual(calendar,[])
            self.assertFalse(diagnostics['calendar_loaded'])
            self.assertIn('File not found',diagnostics['failure_reason'])

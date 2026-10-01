"""Exercise the real localhost handler and engines with isolated synthetic data."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import numpy as np
import pandas as pd


class HTTPTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('MOMENTUM_TEST_REAL_DATA')=='1', 'Opt-in copied ETF dataset integration test')
    def test_known_etf_signal_matches_reconstruction(self):
        import MomentumLabV2 as app
        from momentumlab.live_model import target_snapshot
        # Control from the verified September dataset/audit, before EXE packaging.
        expected=['159208.SZ','512710.SH','159267.SZ','563380.SH','159241.SZ',
                  '520870.SH','159100.SZ','159227.SZ','159046.SZ','159940.SZ']
        strategy=dict(universe='ETF_250M',lookback=10,selection='Top 10',rebalance_days=10,cost_bps=5)
        market=app.live_market(strategy)
        if market['provenance']['dataset_fingerprint']!='f7af8a71b51e42389e4419865fbb4c1ecadee3b77a7ec89f0049e0a66ddf74a1':
            self.skipTest('Local dataset does not match the pinned September ETF control.')
        research=target_snapshot(strategy,market,'2026-09-04')
        self.assertEqual(research['execution_date'],'2026-09-07')
        self.assertEqual(list(research['weights']),expected)
        self.assertEqual(list(research['ranks'].values()),list(range(1,11)))
        reconstructed=app.historical_reconstruction(strategy,'2026-09-04','2026-09-13',dict(run_id='control',history_files=[]))
        first=reconstructed['signals'][0]
        self.assertEqual(first['signal_date'],'2026-09-04')
        self.assertEqual(first['execution_date'],'2026-09-07')
        self.assertEqual(list(first['weights']),expected)
        self.assertEqual((first['universe'],first['momentum_lookback'],first['selection'],first['rebalance_frequency']),('ETF_250M',10,'Top 10',10))
        self.assertTrue(reconstructed['integrity']['research_vs_backtest']['match'])

    def test_grid_to_live_and_exports(self):
        import MomentumLabV2 as app
        from momentumlab.live_service import LiveService
        from momentumlab.live_store import LiveStore
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            dates=pd.bdate_range('2024-01-01',periods=90)
            prices=pd.DataFrame([dict(ts_code=f'{i:06d}',trade_date=d,close=100*np.exp(.001*j+.03*np.sin(j/(i+2))),adj_close=100*np.exp(.001*j+.03*np.sin(j/(i+2)))) for i in range(25) for j,d in enumerate(dates)])
            weights=pd.DataFrame([dict(con_code=f'{i:06d}',trade_date=dates[0],weight=.04) for i in range(25)])
            prices.to_csv(root/'prices.csv',index=False)
            weights.to_csv(root/'weights.csv',index=False)
            pd.DataFrame({'trade_date':dates,'is_open':1}).to_csv(root/'calendar.csv',index=False)
            prices.attrs['exchange_calendar']=tuple(dates)
            paths={'adjusted_price_csv':str(root/'prices.csv'),'weights_csv':str(root/'weights.csv'),'trade_calendar_csv':str(root/'calendar.csv')}
            market=dict(raw=prices,adjusted=prices,calendar=list(dates.strftime('%Y-%m-%d')),data_end=dates[-1].strftime('%Y-%m-%d'),names={},ranked=app.merge_membership_and_rank(app.calculate_signal(prices,10),weights))
            def reconstruct(strategy,start,end,source):
                net,_,history,_=app.backtest_v4(market['ranked'],strategy['selection'],strategy['rebalance_days'],pd.Timestamp(start),pd.Timestamp(end),strategy['cost_bps'])
                return app.research_bundle(history,net,source['run_id'])
            service=LiveService(LiveStore(root/'live.sqlite3'),lambda s:market,reconstruct)
            with patch.object(app,'load_config',return_value={'active_universe':'CSI300','results_folder':str(root)}), patch.object(app,'selected_universe_paths',return_value=paths), patch.object(app,'validate_research_files',return_value={'ok':True}), patch.object(app,'live_service',return_value=service), patch.object(app,'job_snapshot',return_value={'status':'idle'}):
                server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                def post(path,body):
                    try:
                        response = urlopen(Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=60)
                    except HTTPError as exc:
                        self.fail(path + ': ' + exc.read().decode())
                    with response:
                        result=json.loads(response.read(),parse_constant=lambda v: self.fail('Invalid JSON constant: '+v))
                    self.assertTrue(result.get('ok'),result)
                    return result
                try:
                    for path in ['/', '/ui/strategy-grid.js','/ui/live-portfolio.js','/ui/live-portfolio.css']:
                        with urlopen(base+path) as response:self.assertEqual(response.status,200)
                    research=post('/api/run',dict(universe='CSI300',lookback=10,display_spans=[10],selection='Top 5',rebalance_days=5,trading_cost_bps=5))
                    self.assertIsNone(research['rebalance_history'][0]['retention_rate'])
                    grid=post('/api/strategy-grid',dict(universe='CSI300',lookbacks=[10],rebalances=[5],selections=['Top 5','Top 10','Top 20','Q4','Q5'],years=[2024],include_full=True,trading_cost_bps=5))
                    self.assertEqual(len(grid['rows']),10)
                    for row in grid['rows']:
                        self.assertNotIn('error',row)
                        self.assertLessEqual(row['net_return'],row['gross_return'])
                        self.assertTrue(Path(row['history_file']).exists())
                    preview=post('/api/live/preview',dict(run_id=grid['run_id'],lookback=10,selection='Top 5',rebalance_days=5,start_date='2024-02-01',end_date=dates[-1].strftime('%Y-%m-%d'),cost_bps=5))
                    self.assertEqual(preview['signal_date'],'2024-02-01')
                    self.assertEqual(preview['execution_date'],'2024-02-02')
                    self.assertEqual(len(preview['holdings']),5)
                    created=post('/api/live/create',dict(run_id=grid['run_id'],lookback=10,selection='Top 5',rebalance_days=5,name='HTTP pilot',capital=10000,start_date='2024-02-01',status='ACTIVE'))
                    pid=created['id']
                    post('/api/live/trade',dict(id=pid,ticker='000000',side='BUY',date='2024-02-02',quantity=10,price=100,fee=1,request_id='http-trade'))
                    detail=post('/api/live/detail',dict(id=pid,refresh=True))
                    self.assertEqual(detail['stats']['cash'],8999)
                    self.assertFalse(detail['is_paper'])
                    self.assertIn('NAV Snapshot',[e['event_type'] for e in detail['events']])
                    post('/api/live/journal',dict(id=pid,date='2024-02-02',signal_id=detail['signals'][0]['id']))
                    exported=post('/api/live/export',dict(id=pid))
                    self.assertEqual(len(exported['files']),6)
                    for filename in exported['files'].values():self.assertTrue(Path(filename).exists())
                    paper=post('/api/live/create',dict(run_id=grid['run_id'],lookback=10,selection='Top 5',rebalance_days=5,name='HTTP paper',capital=10000,start_date='2024-02-01',status='PAPER'))
                    paper_detail=post('/api/live/detail',dict(id=paper['id'],refresh=True))
                    workflow=paper_detail['rebalance_workflow']
                    self.assertEqual(workflow['status'],'WAITING_FOR_EXECUTION')
                    proposal=post('/api/live/rebalance-preview',dict(id=paper['id'],capital_adjustment='ADD',capital_amount=1000))
                    self.assertEqual(proposal['investable_nav'],paper_detail['stats']['current_nav']+1000)
                    self.assertTrue(all(r['status'] in {'NEW','CARRIED','EXIT'} for r in proposal['recommendations']))
                    applied=post('/api/live/paper-rebalance',dict(id=paper['id'],signal_id=workflow['signal_id'],capital_adjustment='ADD',capital_amount=1000))
                    self.assertEqual(applied['status'],'COMPLETED')
                    funded=post('/api/live/detail',dict(id=paper['id']))
                    self.assertEqual(funded['stats']['external_contributions'],1000)
                    self.assertAlmostEqual(funded['stats']['investment_profit_loss'],funded['stats']['current_nav']-11000)
                finally:
                    server.shutdown();server.server_close();thread.join()

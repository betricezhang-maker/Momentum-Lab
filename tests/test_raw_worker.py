"""Offline worker lifecycle and staged resume regressions."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
import pandas as pd
from test_regressions import functions
from momentumlab.data_integrity import staged_dataset


class RawWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ns=functions()
        self.updates=[]
        self.ns.update(job_update=lambda **kw:self.updates.append(kw),
                       completed_checkpoint=lambda _:set(),load_checkpoint=lambda:{},
                       mark_checkpoint=lambda *args:None)

    def fetch(self,path,start='2026-09-15',end='2026-09-14'):
        return self.ns['fetch_stock_history']('fund_daily',['A'],start,end,'',path,
                                              ['ts_code','trade_date'],'raw','Raw prices')

    def test_current_skips_requests_with_ticker_progress(self):
        path=self.root/'raw.csv'
        pd.DataFrame({'ts_code':['A'],'trade_date':['20260914'],'close':[1]}).to_csv(path,index=False)
        request=Mock(side_effect=AssertionError('Unexpected API request'))
        self.ns['tushare_call']=request
        self.fetch(path)
        self.assertEqual(self.updates[0]['total'],1)
        self.assertIn('ALREADY CURRENT',self.updates[-1]['message'])
        request.assert_not_called()

    def test_bad_local_file_raises_before_first_request(self):
        path=self.root/'raw.csv';path.write_text('')
        self.ns['tushare_call']=Mock()
        with self.assertRaises(pd.errors.EmptyDataError): self.fetch(path)
        self.assertEqual(self.updates[0]['stage'],'Raw prices')
        self.ns['tushare_call'].assert_not_called()

    def test_preparation_scales_and_preserves_gap_start(self):
        days=pd.bdate_range('2021-01-01',periods=1400)
        raw=pd.DataFrame([(c,d) for c in ['A','B','C'] for d in days],columns=['ts_code','trade_date'])
        path=self.root/'raw.csv';raw.to_csv(path,index=False)
        cal=self.root/'cal.csv';pd.DataFrame({'trade_date':days,'is_open':1}).to_csv(cal,index=False)
        old=raw.drop(index=12)
        before=time.monotonic()
        starts=self.ns['incremental_ticker_starts']('fund_daily',old,['A','B','C'],days[-1]+pd.Timedelta(days=1),
                                                   {'raw_price_csv':path,'trade_calendar_csv':cal})
        self.assertEqual(starts['A'],days[12])
        self.assertLess(time.monotonic()-before,4)

    def test_failed_staging_preserves_completed_files_for_resume(self):
        path=self.root/'raw.csv';path.write_text('original')
        paths={'root':str(self.root),'raw_price_csv':str(path)}
        with self.assertRaisesRegex(RuntimeError,'worker'):
            with staged_dataset(paths,resume_id='abc') as work:
                Path(work['raw_price_csv']).write_text('completed download')
                raise RuntimeError('worker failed')
        self.assertEqual(path.read_text(),'original')
        with staged_dataset(paths,resume_id='abc') as work:
            self.assertEqual(Path(work['raw_price_csv']).read_text(),'completed download')

    def test_exception_persists_stage_and_traceback(self):
        state={'status':'running','stage':'Raw prices','stage_results':[{'stage':'Eligibility','status':'PASS'}]}
        self.ns.update(_JOB=state,_JOB_LOCK=threading.Lock(),job_update=lambda **kw:state.update(kw),
                       checkpoint_path=lambda:self.root/'checkpoint.json',write_log=lambda _:None)
        self.ns['record_job_error'](ValueError('broken local file'),'test traceback')
        import json
        saved=json.loads((self.root/'.momentumlab_job_error.json').read_text())
        self.assertEqual(saved['stage'],'Raw prices')
        self.assertEqual(saved['traceback'],'test traceback')
        self.assertEqual(saved['status'],'error')

    def test_dead_worker_is_error_and_has_no_api_counter(self):
        state={'status':'running','stage':'Raw prices','kind':'refresh','request':{'end_date':'2026-09-14'}}
        self.ns.update(_JOB=state,_JOB_LOCK=threading.Lock(),_JOB_THREAD=Mock(is_alive=lambda:False),
                       _API_LOCK=threading.Lock(),_API_TIMES=[],
                       job_update=lambda **kw:state.update(kw),checkpoint_path=lambda:self.root/'checkpoint.json',
                       write_log=lambda _:None)
        result=self.ns['job_snapshot']()
        self.assertEqual(result['status'],'error')
        self.assertFalse(result['worker_active'])
        self.assertIsNone(result['api_requests_last_minute'])
        self.assertIn('Stale job',result['last_error'])

    def test_etf_date_batches_filter_and_dedupe(self):
        path=self.root/'raw.csv'
        pd.DataFrame({'ts_code':['A'],'trade_date':['20260914'],'close':[1]}).to_csv(path,index=False)
        self.ns['tushare_call']=Mock(return_value=pd.DataFrame({'ts_code':['A','B','A'],
            'trade_date':['20260915','20260915','20260915'],'close':[2,3,2]}))
        result=self.ns['fetch_etf_date_batches']('fund_daily',['A'],[pd.Timestamp('2026-09-15')],str(path),
            'ts_code,trade_date,close',['ts_code','trade_date'],'ETF daily prices')
        self.assertEqual(result['requests'],1);self.assertEqual(result['rows_fetched'],2)
        saved=pd.read_csv(path);self.assertEqual(len(saved.drop_duplicates(['ts_code','trade_date'])),2)
        self.assertEqual(result['duplicate_keys'],0)

    def test_etf_date_batches_no_missing_is_zero_work(self):
        path=self.root/'raw.csv'
        pd.DataFrame({'ts_code':['A'],'trade_date':['20260914'],'close':[1]}).to_csv(path,index=False)
        request=Mock(side_effect=AssertionError('unexpected date request'));self.ns['tushare_call']=request
        result=self.ns['fetch_etf_date_batches']('fund_daily',['A'],[],str(path),'ts_code,trade_date,close',
            ['ts_code','trade_date'],'ETF daily prices')
        self.assertEqual(result['status'],'SKIPPED / ALREADY CURRENT');request.assert_not_called()

    def test_incremental_open_sessions_uses_calendar_and_latest_date(self):
        cal=self.root/'cal.csv';raw=self.root/'raw.csv'
        pd.DataFrame({'trade_date':['20260914','20260915','20260916','20260917'],'is_open':[1,1,1,1]}).to_csv(cal,index=False)
        pd.DataFrame({'ts_code':['A'],'trade_date':['20260914']}).to_csv(raw,index=False)
        self.assertEqual([str(x.date()) for x in self.ns['_incremental_open_sessions'](str(cal),str(raw),'2026-09-17')],
                         ['2026-09-15','2026-09-16','2026-09-17'])

    def test_refresh_retry_keeps_calendar_and_eligibility(self):
        import json
        import uuid
        from momentumlab.data_integrity import validate_calendar
        paths={'root':str(self.root)}
        for key in ['raw_price_csv','adj_factor_csv','trade_calendar_csv','weights_csv','adjusted_price_csv']:
            paths[key]=str(self.root/(key+'.csv'))
        for key in ['raw_price_csv','adj_factor_csv']:
            pd.DataFrame({'trade_date':['20260911'],'ts_code':['A']}).to_csv(paths[key],index=False)
        calls=[];cp={}
        def init(kind,request):
            cp.setdefault('signature',{'kind':kind,**request});return cp.copy()
        def save(value): cp.clear();cp.update(value)
        def calendar(start,end,path):
            calls.append('calendar')
            pd.DataFrame({'trade_date':['20260914'],'is_open':[1]}).to_csv(path,index=False)
        def eligibility(*args): calls.append('eligibility')
        def fail(*args,**kwargs): raise RuntimeError('before request')
        self.ns.update(load_config=lambda:{'active_universe':'ETF_250M'},default_data_paths=lambda *a:paths,
                       init_checkpoint=init,load_checkpoint=lambda:cp.copy(),save_checkpoint=save,
                       staged_dataset=staged_dataset,validate_calendar=validate_calendar,
                       fetch_trade_calendar=calendar,build_etf_eligibility=eligibility,
                       infer_tickers_from_weights=lambda _:['A'],fetch_etf_date_batches=fail,
                       build_adjusted_prices=Mock(side_effect=AssertionError('must not rebuild')),
                       write_log=lambda _:None)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,'before request'):
                self.ns['refresh_data']('2026-09-14')
        self.assertEqual(calls,['calendar','eligibility'])
        self.ns['build_adjusted_prices'].assert_not_called()

if __name__=='__main__': unittest.main()

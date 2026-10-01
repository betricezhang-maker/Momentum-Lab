import tempfile
import unittest
import threading
import weakref
from pathlib import Path
import pandas as pd
from pandas.testing import assert_series_equal
from test_regressions import functions
import test_regressions as regression_fixture
from momentumlab.dates import normalize_dates
from momentumlab.runtime_cache import RuntimeCache, read_small_or_direct, clear_small_caches


class PerformanceTests(unittest.TestCase):
    def tearDown(self):clear_small_caches()

    def test_small_file_cache_copy_isolation_invalidation_and_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'calendar.csv';path.write_text('trade_date,is_open\n20260904,1\n')
            calls=[]
            def read(*args,**kw):calls.append(1);return pd.read_csv(*args,**kw)
            first=read_small_or_direct(path,read);first.loc[0,'is_open']=0
            self.assertEqual(read_small_or_direct(path,read).iloc[0].is_open,1)
            self.assertEqual(len(calls),1)
            path.write_text('trade_date,is_open\n20260904,1\n20260907,1\n')
            self.assertEqual(len(read_small_or_direct(path,read)),2)
            self.assertEqual(len(calls),2)
            clear_small_caches();read_small_or_direct(path,read);self.assertEqual(len(calls),3)

    def test_cache_byte_bound_and_idle_expiry_release_objects(self):
        class Object:pass
        cache=RuntimeCache(max_bytes=10,max_entries=2,ttl=.03)
        value=Object();reference=weakref.ref(value)
        cache.put('a',value,6);del value
        cache.put('b',Object(),6)
        self.assertIsNone(reference())
        self.assertIsNone(cache.get('a'))
        value=Object();reference=weakref.ref(value);cache.put('c',value,5);del value
        threading.Event().wait(.15)
        self.assertIsNone(reference()) # Automatic expiry, with no cache access.
        self.assertEqual(cache.items(),[])

    def test_datetime_fast_path_is_identical_to_string_path(self):
        for dates in [pd.Series(pd.to_datetime(['2026-09-04 12:30',None])),
                      pd.Series(pd.to_datetime(['2026-09-04 23:30+08:00',None],utc=True)),
                      pd.Series(pd.date_range('2026-01-01',periods=3).as_unit('s'))]:
            assert_series_equal(normalize_dates(dates),normalize_dates(dates.astype('string')))

    def test_status_is_cached_and_preserves_row_counts(self):
        ns=functions()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'prices.csv';path.write_text('ts_code,trade_date,close\nA,20260904,1\nB,20260904,2\n')
            first=ns['inspect_csv'](str(path),'raw')
            self.assertEqual(first['rows'],2);self.assertEqual(first['extra'],'2 stocks')
            self.assertEqual(first['start'],'2026-09-04')
            self.assertEqual(ns['inspect_csv'](str(path),'raw'),first)
            path.write_text('ts_code,trade_date,close\nC,20260907,3\n')
            self.assertEqual(ns['inspect_csv'](str(path),'raw')['rows'],1)

    def test_full_research_reuses_ranks_and_preserves_snapshot_and_monitor(self):
        fixture=regression_fixture.RegressionTests();fixture.setUp();self.addCleanup(fixture.doCleanups);fixture.grid_fixture()
        ns=fixture.ns;ns.update(save_heatmap=lambda *a,**kw:None,save_line=lambda *a,**kw:None)
        original=ns['calculate_signal'];calls=[]
        def calculate(prices,span):calls.append(span);return original(prices,span)
        ns['calculate_signal']=calculate
        result=ns['run_research'](dict(universe='CSI300',lookback=10,display_spans=[10,20],selection='Top 5',rebalance_days=10))
        self.assertEqual(calls,[10,20]);self.assertEqual(result['snapshots'],1)
        prices=ns['read_csv'](fixture.root/'prices.csv');weights=ns['read_csv'](fixture.root/'weights.csv')
        prices.attrs['exchange_calendar']=tuple(pd.read_csv(fixture.root/'calendar.csv').trade_date)
        expected=ns['multi_span_monitor'](prices,weights,[10,20],10)
        self.assertEqual(result['top20'],expected)


if __name__=='__main__':unittest.main()

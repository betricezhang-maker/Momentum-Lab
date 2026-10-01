import tempfile
import unittest
from pathlib import Path
import pandas as pd
from momentumlab.benchmark import load_benchmark, compare_equity


class BenchmarkTests(unittest.TestCase):
    def test_matching_dates_compounded_and_no_forward_fill(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'b.csv'
            pd.DataFrame({'ts_code':['ETF']*3,'trade_date':['2024-01-02','2024-01-03','2024-01-04'],'close':[100,110,121]}).to_csv(p,index=False)
            b,meta=load_benchmark(p,'ETF','Example ETF','adjusted_etf_return')
            curves,stats,cov=compare_equity(pd.Series([1,1.05,1.1],index=pd.to_datetime(['2024-01-02','2024-01-03','2024-01-04'])),b)
            self.assertEqual(meta['basis'],'adjusted_etf_return');self.assertAlmostEqual(stats['benchmark']['cumulative_return'],.21)
            self.assertEqual((cov['actual_start'],cov['actual_end']),('2024-01-02','2024-01-04'))
    def test_missing_benchmark_date_is_dropped_from_comparison_not_filled(self):
        b=pd.Series([100,121],index=pd.to_datetime(['2024-01-02','2024-01-04']))
        s=pd.Series([1,1.1,1.2],index=pd.to_datetime(['2024-01-02','2024-01-03','2024-01-04']))
        curves,stats,cov=compare_equity(s,b)
        self.assertEqual(cov['comparison_observations'],2)
        self.assertEqual(curves.index.strftime('%Y-%m-%d').tolist(),['2024-01-02','2024-01-04'])

import unittest,tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal,assert_series_equal
from test_etf_momentum_monitor import ETFMonitorTests
from momentumlab.rank_weighting import study,save_report,weights_for,replay

class RankWeightingTests(unittest.TestCase):
    def test_reverse_weights_are_exact_mirrors_with_same_concentration(self):
        for alpha in (.5,1):
            forward=weights_for(alpha);reverse=weights_for(alpha,True)
            np.testing.assert_array_equal(reverse,forward[::-1])
            self.assertTrue(np.all(np.diff(reverse)>0))
            self.assertAlmostEqual(reverse.sum(),1.)
            self.assertAlmostEqual(sum(reverse**2),sum(forward**2))
        self.assertAlmostEqual(weights_for(1,True)[0],1/55)
        self.assertAlmostEqual(weights_for(1,True)[9],10/55)
    def fixture(self):
        f=ETFMonitorTests();f.setUp();return f
    def test_reconciliation_weights_turnover_and_no_mutation(self):
        f=self.fixture();before=f.ranked.copy(deep=True);eq=f.eq.copy()
        r,c,t=study(f.ranked,f.log,f.eq,f.prices)
        self.assertEqual(len(c.columns),5)
        self.assertLess(r['reconciliation_error'],1e-9)
        assert_frame_equal(f.ranked,before);assert_series_equal(f.eq,eq)
        for alpha in (0,.5,1):self.assertAlmostEqual(weights_for(alpha).sum(),1)
        self.assertTrue(np.all(np.diff(weights_for(1))<0));self.assertEqual(len(t['rank_observations']),(len(f.log)-1)*10)
        self.assertTrue((t['rank_observations'].signal_date<t['rank_observations'].execution_date).all())
        for row in t['rebalance_records'].itertuples():
            import json
            old=json.loads(row.old_weights);target=json.loads(row.target_weights)
            turn=.5*sum(abs(target.get(k,0)-old.get(k,0)) for k in set(old)|set(target))
            self.assertAlmostEqual(turn,row.one_way_turnover);self.assertAlmostEqual(row.cost,row.nav_before*turn*.0005)
        for method,g in t['annual_comparison'].groupby('method'):
            self.assertAlmostEqual(np.prod(1+g.net_return),c[method].iloc[-1])
    def test_mismatch_stops_interpretation(self):
        f=self.fixture();wrong=f.eq.copy();wrong.iloc[5]*=1.01
        with self.assertRaisesRegex(ValueError,'reconstruction mismatch'):study(f.ranked,f.log,wrong,f.prices)
        h=f.log.copy();h.loc[0,'tickers']=','.join(reversed(h.loc[0,'tickers'].split(',')))
        with self.assertRaisesRegex(ValueError,'rank order'):study(f.ranked,h,f.eq,f.prices)
    def test_missing_price_excluded_and_replay_matches_production(self):
        f=self.fixture();ticker=f.log.iloc[0].tickers.split(',')[0];date=pd.Timestamp(f.log.iloc[0].entry_date)+pd.offsets.BDay(2)
        ranked=f.ranked.copy();ranked.loc[ranked.ts_code.eq(ticker)&ranked.trade_date.eq(date),'adj_close']=np.nan
        eq,_,h,_=f.ns['backtest_v4'](ranked,'Top 10',10,None,None,5)
        r,c,t=study(ranked,h,eq,f.prices)
        bad=t['rank_observations'].query('ticker == @ticker').iloc[0]
        self.assertTrue(pd.isna(bad.holding_return));self.assertIn('Excluded',bad.validity)
        self.assertTrue(any('provisional' in n for n in r['notes']))
    def test_future_prices_cannot_change_earlier_observations(self):
        f=self.fixture();_,_,t=study(f.ranked,f.log,f.eq,f.prices);cut=f.days[60]
        prices=f.prices.copy();prices.loc[prices.trade_date>cut,'adj_close']*=1.5;prices.attrs=f.prices.attrs
        ranked=f.rank(prices,f.weights);eq,_,h,_=f.ns['backtest_v4'](ranked,'Top 10',10,None,None,5)
        _,_,other=study(ranked,h,eq,prices)
        a=t['rank_observations'];b=other['rank_observations'];assert_frame_equal(a[a.holding_end_date<=cut].reset_index(drop=True),b[b.holding_end_date<=cut].reset_index(drop=True))
    def test_artifacts(self):
        f=self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            r=save_report(f.ranked,f.log,f.eq,f.prices,5,folder,{'run_id':'synthetic'})
            self.assertTrue(all(Path(p).is_file() for p in r['files'].values()))
            self.assertEqual(len(r['charts']),4);self.assertTrue(all(Path(p).stat().st_size>0 for p in r['charts'].values()))

if __name__=='__main__':unittest.main()

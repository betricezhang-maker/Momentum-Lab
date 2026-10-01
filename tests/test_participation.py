import unittest
import numpy as np
import pandas as pd
from pathlib import Path
from pandas.testing import assert_frame_equal
from momentumlab.participation import compare

class ParticipationTests(unittest.TestCase):
    def test_membership_date_encodings_use_only_past_snapshot(self):
        signals=pd.to_datetime(['2021-01-29','2021-02-19','2021-03-05'])
        entries=signals+pd.offsets.BDay(1)
        nav=pd.Series(1.,index=pd.bdate_range(entries[0],entries[-1]))
        h=pd.DataFrame(dict(signal_date=signals,entry_date=entries,nav_before=1.))
        ranked=pd.DataFrame([dict(trade_date=d,ts_code=t,momentum_score=v,signal_window_valid=True) for d in signals for t,v in [('A',1.),('B',-1.),('FUTURE',1.)]])
        dates=['20210129','20210129','20210219','20210901']
        expected=None
        for encoded in [list(map(int,dates)),dates,pd.to_datetime(dates,format='%Y%m%d'),pd.to_datetime(dates,format='%Y%m%d').strftime('%Y-%m-%d')]:
            w=pd.DataFrame(dict(trade_date=encoded,con_code=['A','B','B','FUTURE']))
            original=w.copy(deep=True)
            result,curve,periods=compare(ranked,w,h,nav,.3)
            self.assertEqual(periods.eligible_count.tolist(),[2,1])
            self.assertEqual(periods.participation.tolist(),[.5,0.])
            self.assertEqual(periods.cash.tolist(),[False,True])
            self.assertEqual(result['stats']['fallback_periods'],0)
            assert_frame_equal(w,original)
            if expected is None:expected=periods
            else:assert_frame_equal(periods,expected)

    def test_chart_uses_exact_existing_curves(self):
        import tempfile
        import matplotlib.pyplot as plt
        from unittest.mock import patch
        from momentumlab.participation import save_comparison_chart
        c=pd.DataFrame({'baseline':[1.,1.1,1.2],'cash_warning':[1.,1.,1.1]},index=pd.bdate_range('2024-01-01',periods=3));before=c.copy()
        result=dict(coverage=0,lookback=5,selection='Top 20',rebalance_days=5,threshold=.3,stats={'return_difference':-.1})
        with tempfile.TemporaryDirectory() as folder,patch.object(plt,'close'):
            path=Path(folder)/'comparison.png';save_comparison_chart(c,result,path)
            self.assertTrue(path.is_file());ax=plt.gcf().axes[0]
            np.testing.assert_array_equal(ax.lines[0].get_ydata(),(c.baseline-1)*100)
            np.testing.assert_array_equal(ax.lines[1].get_ydata(),(c.cash_warning-1)*100)
            self.assertIn('MOM5',ax.get_title());self.assertIn('30%',ax.get_title());assert_frame_equal(c,before)
        plt.close('all')
    def test_threshold_coverage_and_cost(self):
        from test_cash_warning import CashWarningTests
        obs,nav,h=CashWarningTests().fixture()
        h['signal_date']=h.entry_date-pd.offsets.BDay(1)
        ranked=pd.DataFrame([dict(trade_date=d,ts_code=t,momentum_score=v,signal_window_valid=True) for d in h.signal_date for t,v in [('A',1),('B',-1)]])
        weights=pd.DataFrame(dict(trade_date=[h.signal_date.min()]*2,con_code=['A','B']))
        result,c,d=compare(ranked,weights,h,nav,.5);self.assertTrue(d.cash.all());self.assertAlmostEqual(c.cash_warning.iloc[-1],.99975)
        result,c,d=compare(ranked,weights,h,nav,.4);np.testing.assert_array_equal(c.baseline,c.cash_warning)
        ranked.momentum_score=-1;self.assertTrue(compare(ranked,weights,h,nav,0)[2].cash.all())
        ranked.momentum_score=1;self.assertTrue(compare(ranked,weights,h,nav,1)[2].cash.all())
        ranked.loc[ranked.ts_code=='A','signal_window_valid']=False
        result,c,d=compare(ranked,weights,h,nav,1);self.assertFalse(d.cash.any());self.assertEqual(result['stats']['fallback_periods'],len(d))
        result,c,d=compare(ranked,weights,h,nav,1,coverage=0);self.assertTrue(d.cash.all());self.assertTrue(d.valid_count.eq(1).all())
        ranked.signal_window_valid=False
        result,c,d=compare(ranked,weights,h,nav,1,coverage=0);self.assertFalse(d.cash.any());self.assertTrue(d.participation.isna().all())
        with self.assertRaises(ValueError):compare(ranked,weights,h,nav,.55)

    def test_selected_combinations_and_baseline_unchanged(self):
        from test_regressions import RegressionTests
        f=RegressionTests();f.setUp();self.addCleanup(f.doCleanups);f.grid_fixture();ns=f.ns
        ns.update(save_heatmap=lambda *a,**k:None,save_line=lambda *a,**k:None)
        for span,selection,rebalance in [(5,'Top 5',5),(10,'Top 20',10)]:
            args=dict(universe='CSI300',lookback=span,display_spans=[span],selection=selection,rebalance_days=rebalance)
            base=ns['run_research'](args);test=ns['run_research']({**args,'participation_threshold':.5})
            pc=test['participation_control'];self.assertEqual(pc['status'],'available',pc)
            self.assertEqual((pc['lookback'],pc['selection'],pc['rebalance_days']),(span,selection,rebalance))
            assert_frame_equal(pd.read_csv(Path(base['files']['folder'])/'equity.csv'),pd.read_csv(Path(test['files']['folder'])/'equity.csv'))

if __name__=='__main__':unittest.main()

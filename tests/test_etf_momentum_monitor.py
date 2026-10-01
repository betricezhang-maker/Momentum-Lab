import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal,assert_series_equal
from test_regressions import functions


class ETFMonitorTests(unittest.TestCase):
    def setUp(self):
        self.ns=functions();self.days=pd.bdate_range('2024-01-01',periods=90)
        self.prices=pd.DataFrame([dict(ts_code=f'T{i}',trade_date=d,adj_close=100*np.exp(.001*i*k+.03*np.sin(k/(i+2)))) for i in range(12) for k,d in enumerate(self.days)])
        self.prices.attrs['exchange_calendar']=tuple(self.days)
        self.weights=pd.DataFrame(dict(con_code=[f'T{i}' for i in range(12)],trade_date=self.days[0],weight=1/12))
        self.ranked=self.rank(self.prices,self.weights)
        self.eq,_,self.log,_=self.ns['backtest_v4'](self.ranked,'Top 10',10,None,None,5)

    def rank(self,p,w):return self.ns['merge_membership_and_rank'](self.ns['calculate_signal'](p,10),w)
    def observe(self,r=None,w=None,h=None,selection='Top 10'):
        return self.ns['etf_momentum_observations'](self.ranked if r is None else r,self.ranked,self.weights if w is None else w,self.log if h is None else h,selection,self.prices)

    def test_exact_signal_dates_scores_and_no_mutation(self):
        eq=self.eq.copy();ranked=self.ranked.copy(deep=True);log=self.log.copy(deep=True)
        result=self.observe()
        self.assertEqual(list(result.signal_date),list(pd.to_datetime(self.log.signal_date)))
        self.assertTrue((result.signal_date<result.execution_date).all())
        for row in result.itertuples():
            scores=self.ranked[self.ranked.trade_date.eq(row.signal_date)].set_index('ts_code').momentum_score
            self.assertAlmostEqual(row.positive_participation,float(scores.gt(0).mean()))
            self.assertAlmostEqual(row.average_top10_mom10,scores.loc[row.selected_tickers.split(',')].mean())
        assert_series_equal(eq,self.eq);assert_frame_equal(ranked,self.ranked);assert_frame_equal(log,self.log)
        self.assertTrue(result.loc[result.signal_date<self.days[60],'median_return60'].isna().all())

    def test_median_and_holding_returns(self):
        result=self.observe();row=result[result.signal_date>=self.days[60]].iloc[0]
        px=self.prices.pivot(index='trade_date',columns='ts_code',values='adj_close');i=self.days.get_loc(row.signal_date)
        self.assertAlmostEqual(row.median_return60,float((px.iloc[i]/px.iloc[i-60]-1).median()))
        self.assertAlmostEqual(result.iloc[0].subsequent_net_return,self.log.iloc[1].nav_before/self.log.iloc[0].nav_before-1)
        self.assertTrue(pd.isna(result.iloc[-1].subsequent_net_return))
        p=self.prices[~(self.prices.ts_code.isin(['T0','T1','T2'])&self.prices.trade_date.eq(self.days[50]))].copy();p.attrs=self.prices.attrs
        altered=self.ns['etf_momentum_observations'](self.ranked,self.ranked,self.weights,self.log,'Top 10',p)
        r=altered[altered.signal_date.eq(row.signal_date)].iloc[0]
        self.assertEqual(r.valid_return60_count,9);self.assertTrue(pd.isna(r.median_return60));self.assertIn('80%',r.median_return60_status)

    def test_missing_coverage_and_non_top10_explicit(self):
        r=self.ranked.copy();r.loc[r.ts_code.eq('T0'),'momentum_score']=np.nan
        result=self.observe(r=r)
        self.assertTrue(result.positive_participation.notna().all());self.assertTrue((result.excluded_count==1).all())
        for row in result.itertuples():self.assertAlmostEqual(row.positive_participation,row.positive_count/row.valid_mom10_count)
        self.assertTrue(self.observe(selection='Top 20').average_top10_mom10.isna().all())
        h=self.log.copy();h.loc[0,'tickers']=','.join(h.loc[0,'tickers'].split(',')[:-1])
        self.assertTrue(pd.isna(self.observe(h=h).iloc[0].average_top10_mom10))

    def test_participation_threshold_boundary_configuration_and_empty(self):
        w=self.weights.iloc[:10];r=self.ranked.copy();r.loc[r.ts_code.isin(['T0','T1']),'momentum_score']=np.nan
        def check(scores,threshold=.8):
            return self.ns['etf_momentum_observations'](scores,self.ranked,w,self.log,'Top 10',self.prices,participation_min_coverage=threshold)
        exact=check(r);self.assertTrue((exact.coverage==.8).all());self.assertTrue(exact.positive_participation.notna().all())
        self.assertTrue(check(r,.81).positive_participation.isna().all())
        r.loc[r.ts_code.eq('T2'),'momentum_score']=np.nan
        below=check(r);self.assertTrue(below.positive_participation.isna().all());self.assertTrue((below.excluded_count==3).all())
        self.assertTrue(check(r,0).positive_participation.notna().all())
        r['momentum_score']=np.nan;self.assertTrue(check(r).positive_participation.isna().all())
        self.assertTrue(check(r,0).positive_participation.isna().all())
        for threshold in [-.1,1.1,float('nan')]:
            with self.assertRaises(ValueError):check(r,threshold)

    def test_point_in_time_membership_and_future_prices(self):
        cutoff=self.days[50];p=self.prices.copy();p.loc[p.trade_date>cutoff,'adj_close']*=2
        w=pd.concat([self.weights,self.weights.iloc[:3].assign(trade_date=self.days[60])])
        original=self.observe();changed=self.observe(r=self.rank(p,w),w=w)
        assert_frame_equal(original[original.signal_date<=cutoff],changed[changed.signal_date<=cutoff])
        changed=self.ns['etf_momentum_observations'](self.rank(p,w),self.ranked,w,self.log,'Top 10',p)
        assert_frame_equal(original[original.signal_date<=cutoff][['median_return60','valid_return60_count']],changed[changed.signal_date<=cutoff][['median_return60','valid_return60_count']])
        early=self.weights.assign(trade_date=self.days[60]);missing=self.observe(w=early)
        self.assertTrue(missing.loc[missing.signal_date<self.days[60],'positive_participation'].isna().all())

    def test_four_axes_share_range_and_net_curve_exact(self):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from unittest.mock import patch
        self.ns['plt']=plt
        with tempfile.TemporaryDirectory() as folder,patch.object(plt,'close'):
            self.ns['save_etf_momentum_chart'](self.eq,self.observe(),Path(folder)/'chart.png','Synthetic ETF strategy')
            fig=plt.gcf();self.assertEqual(len(fig.axes),4)
            self.assertTrue(all(a.get_xlim()==fig.axes[0].get_xlim() for a in fig.axes))
            np.testing.assert_array_equal(fig.axes[0].lines[0].get_ydata(),(self.eq-1)*100)
            self.assertEqual(len(fig.axes[0].lines[0].get_xdata()),len(self.eq))
            self.assertEqual(len(fig.axes[1].collections[0].get_offsets()),len(self.log))
            for ax,col,scale in zip(fig.axes[1:],['positive_participation','average_top10_mom10','median_return60'],[100,1,100]):
                np.testing.assert_allclose(ax.lines[0].get_ydata(),pd.to_numeric(self.observe()[col],errors='coerce')*scale,equal_nan=True)
            self.assertTrue((Path(folder)/'chart.png').is_file())
        plt.close('all')

    def test_visual_median_gaps_and_outcome_alignment(self):
        obs=pd.DataFrame({'indicator':[1,5,3,2,4,np.nan,6,7,8,9,10],
            'subsequent_net_return':[.1,-.2,0,np.nan,.9,.1,.1,.1,.1,.1,.1],
            'holding_return_status':['Complete: saved']*4+['Final holding period not complete']+['Complete: saved']*6})
        values,median,returns,colors=self.ns['etf_visual_series'](obs,'indicator')
        self.assertEqual(median.iloc[4],3);self.assertTrue(median.iloc[5:10].isna().all());self.assertEqual(median.iloc[10],8)
        self.assertEqual(list(colors[:5]),['#16834a','#c53939','#888888','#888888','#888888'])
        self.assertTrue(pd.isna(returns.iloc[4]));self.assertTrue(pd.isna(values.iloc[5]))
        future=obs.copy();future.loc[6:,'indicator']=1000
        pd.testing.assert_series_equal(median.iloc[:6],self.ns['etf_visual_series'](future,'indicator')[1].iloc[:6])

    def test_scatter_pairs_match_existing_returns(self):
        import matplotlib.pyplot as plt
        from unittest.mock import patch
        self.ns['plt']=plt;obs=self.observe()
        with tempfile.TemporaryDirectory() as folder,patch.object(plt,'close'):
            self.ns['save_etf_momentum_scatter'](obs,Path(folder)/'scatter.png')
            for ax,col,scale in zip(plt.gcf().axes,['positive_participation','average_top10_mom10','median_return60'],[100,1,100]):
                v,_,r,_=self.ns['etf_visual_series'](obs,col);good=v.notna()&r.notna()
                np.testing.assert_allclose(ax.collections[0].get_offsets(),np.c_[v[good]*scale,r[good]*100])
                self.assertIn(f'n = {good.sum()}',ax.get_title())
        plt.close('all')

    def test_auto_integration_etf_only(self):
        from test_regressions import RegressionTests
        f=RegressionTests();f.setUp();self.addCleanup(f.doCleanups);f.grid_fixture();ns=f.ns
        ns.update(save_heatmap=lambda *a,**k:None,save_line=lambda *a,**k:None,save_etf_momentum_chart=lambda *a,**k:None,save_etf_momentum_scatter=lambda *a,**k:None)
        args=dict(universe='CSI300',lookback=10,display_spans=[10],selection='Top 10',rebalance_days=10)
        standard=ns['run_research'](args);self.assertIsNone(standard['etf_momentum_monitor'])
        w=pd.read_csv(f.root/'weights.csv');w['market_cap_rmb']=300000000;w.to_csv(f.root/'weights.csv',index=False)
        pd.DataFrame({'ts_code':w.con_code,'name':w.con_code,'fund_type':'stock'}).to_csv(f.root/'fund_classification.csv',index=False)
        ns['UNIVERSES']['ETF_250M']={'label':'Synthetic ETF'}
        ns['load_config']=lambda:dict(active_universe='ETF_250M',results_folder=str(f.root))
        report=ns['run_research']({**args,'universe':'ETF_250M'})
        self.assertEqual(report['etf_momentum_monitor']['status'],'available')
        a=pd.read_csv(Path(standard['files']['folder'])/'equity.csv')
        b=pd.read_csv(Path(report['files']['folder'])/'equity.csv');assert_frame_equal(a,b)
        self.assertTrue(Path(report['etf_momentum_monitor']['csv']).is_file())


if __name__=='__main__':unittest.main()

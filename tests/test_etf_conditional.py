import unittest
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from momentumlab.etf_conditional import analyze

class ConditionalTests(unittest.TestCase):
    def data(self):
        dates=pd.bdate_range('2020-01-01',periods=1200)
        return pd.DataFrame(dict(signal_date=dates[0:1100:10],execution_date=dates[1:1101:10],holding_end_date=dates[11:1111:10],positive_participation=np.tile([.2,.8],55),median_return60=np.tile([-.1,-.1,.1,.1,.1],22),average_top10_mom10=np.sin(np.arange(110))+2,subsequent_net_return=np.sin(np.arange(110))*.03,holding_return_status='Complete: saved'))
    def test_past_only_and_source_unchanged(self):
        source=self.data();before=source.copy(deep=True);d,r=analyze(source)
        changed=source.copy();changed.loc[60:,'positive_participation']=.99;changed.loc[60:,'average_top10_mom10']=500
        d2,_=analyze(changed);assert_frame_equal(d.iloc[:60],d2.iloc[:60]);assert_frame_equal(source,before)
        self.assertTrue(d.participation_threshold.iloc[:20].isna().all());self.assertEqual(d.participation_threshold.iloc[20],.5)
    def test_zero_missing_incomplete_and_outlier(self):
        source=self.data();source.loc[22,'median_return60']=0;source.loc[23,'positive_participation']=np.nan
        source.loc[24,'holding_return_status']='Incomplete';source.loc[30,'subsequent_net_return']=.9
        d,r=analyze(source);self.assertFalse(d.loc[22:24,'eligible'].any())
        self.assertEqual(r['outlier']['net_return'],.9)
        original=sum(x['n'] for x in r['summary'] if x['period']=='All' and x['mom_bin']=='All' and x['mode']=='Original')
        sensitivity=sum(x['n'] for x in r['summary'] if x['period']=='All' and x['mom_bin']=='All' and x['mode']!='Original')
        self.assertEqual(original-sensitivity,1)
        self.assertTrue(any(x['period']=='2022' for x in r['summary']))
    def test_overlap_suppresses_ci_and_determinism(self):
        source=self.data();_,a=analyze(source);_,b=analyze(source);self.assertEqual(a,b)
        source.loc[25,'holding_end_date']=source.loc[28,'holding_end_date']
        _,r=analyze(source);self.assertTrue(r['overlapping_windows']);self.assertTrue(all(x['mean_ci95'] is None for x in r['summary']))
    def test_summary_matches_raw_distribution(self):
        d,r=analyze(self.data())
        for x in r['summary']:
            if x['mode']!='Original' or x['period']!='All' or x['mom_bin']!='All':continue
            v=d.loc[d.eligible & d.group.eq(x['group']),'subsequent_net_return']
            self.assertEqual(len(v),x['n']);self.assertAlmostEqual(v.mean(),x['mean']);self.assertAlmostEqual(v.median(),x['median'])

if __name__=='__main__':unittest.main()

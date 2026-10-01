import unittest
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from momentumlab.cash_warning import study,economic_path,warnings_for

class CashWarningTests(unittest.TestCase):
    def fixture(self):
        days=pd.bdate_range('2020-01-01',periods=1100)
        nav=pd.Series(np.exp(np.arange(len(days))*.0002+.02*np.sin(np.arange(len(days))/11)),index=days)
        entries=days[1:1092:10];h=pd.DataFrame(dict(entry_date=entries,nav_before=nav.reindex(entries).to_numpy()))
        n=len(entries)-1
        d=pd.DataFrame(dict(signal_date=days[0:n*10:10],execution_date=entries[:-1],holding_end_date=entries[1:],positive_participation=(np.sin(np.arange(n))+1)/2,average_top10_mom10=np.cos(np.arange(n))+1,median_return60=np.sin(np.arange(n)/2)*.1,subsequent_net_return=h.nav_before.to_numpy()[1:]/h.nav_before.to_numpy()[:-1]-1,holding_return_status='Complete: saved'))
        return d,nav,h
    def test_past_only_folds_and_no_mutation(self):
        d,nav,h=self.fixture();before=d.copy(deep=True);r,_=study(d,nav,h)
        assert_frame_equal(d,before)
        for f in r['folds']:
            if f['status'].startswith('Evaluated'):self.assertLess(f['last_training_end'],f['cutoff'])
        later=d.copy();later.loc[later.signal_date.dt.year>=2023,'positive_participation']=.99
        r2,_=study(later,nav,h)
        self.assertEqual([x for x in r['decisions'] if x['year']<2023],[x for x in r2['decisions'] if x['year']<2023])
    def test_no_warning_exact_baseline_and_switch_cost(self):
        d,nav,h=self.fixture();frame=d.iloc[:5];flags=pd.Series(False,index=frame.index);hh=h.set_index('entry_date')
        curve,s=economic_path(frame,flags,nav,hh,.001)
        np.testing.assert_array_equal(curve.baseline,curve.cash_warning);self.assertEqual(s['switches'],0)
        flags[:]=True;curve,s=economic_path(frame,flags,nav,hh,.001)
        self.assertAlmostEqual(curve.cash_warning.iloc[-1],.999);self.assertEqual(s['switches'],1)
        flags.iloc[2:]=False;_,s=economic_path(frame,flags,nav,hh,.001);self.assertEqual(s['switches'],2)
    def test_missing_indicator_not_warning_outcome_not_used_and_overlap(self):
        d,nav,h=self.fixture();d.loc[30,'positive_participation']=np.nan
        flags,a=warnings_for(d,[.4,1.],'Low participation');self.assertFalse(flags.loc[30]);self.assertFalse(a.loc[30])
        other=d.copy();other.subsequent_net_return=999
        f2,_=warnings_for(other,[.4,1.],'Low participation');pd.testing.assert_series_equal(flags,f2)
        d.loc[30,'holding_end_date']=d.loc[32,'holding_end_date']
        with self.assertRaisesRegex(ValueError,'Overlapping'):study(d,nav,h)
    def test_missing_outcomes_suppress_ci_and_final_excluded(self):
        d,nav,h=self.fixture();d.loc[35,'holding_return_status']='Unavailable: price';d.loc[35,'subsequent_net_return']=np.nan
        d.loc[len(d)-1,'holding_end_date']=pd.NaT
        r,_=study(d,nav,h)
        self.assertTrue(all(x['mean_benefit_ci95'] is None for x in r['aggregate']))
        self.assertTrue(all(pd.Timestamp(x['signal_date'])!=d.signal_date.iloc[-1] for x in r['decisions']))
    def test_saved_return_mismatch_rejected(self):
        d,nav,h=self.fixture();d.loc[30,'subsequent_net_return']=.99
        with self.assertRaisesRegex(ValueError,'disagrees'):study(d,nav,h)

if __name__=='__main__':unittest.main()

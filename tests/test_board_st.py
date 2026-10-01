"""Offline point-in-time ST eligibility tests; no provider or production writes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import pandas as pd
from test_regressions import functions


class BoardSTTests(unittest.TestCase):
    def test_dated_exclusion_precedes_top100_and_replaces_old_snapshot(self):
        for universe in ('CHINEXT_TOP100', 'STAR_TOP100'):
            with self.subTest(universe=universe), tempfile.TemporaryDirectory() as folder:
                ns=functions(); codes=[f'T{i:03}' for i in range(102)]
                path=Path(folder)/'membership.csv'
                pd.DataFrame([dict(trade_date='20240131',con_code='T000',weight=0,total_mv=999)]).to_csv(path,index=False)
                def provider(api,params,fields):
                    self.assertEqual(params,{'trade_date':'20240131'})
                    if api=='stock_st':
                        return pd.DataFrame([dict(ts_code='T000',trade_date='20240131',name='*ST example')])
                    return pd.DataFrame(dict(ts_code=codes,trade_date=['20240131']*102,total_mv=list(range(102,0,-1))))
                marker=Mock()
                ns.update(board_codes=lambda _:set(codes),month_end_trade_dates=lambda *a:[pd.Timestamp('2024-01-31')],
                          completed_checkpoint=lambda _:set(),mark_checkpoint=marker,tushare_call=provider,
                          UNIVERSES={universe:{'label':universe}})
                ns['build_top100_membership'](universe,'2024-01-01','2024-01-31',path)
                result=pd.read_csv(path)
                self.assertEqual(len(result),100)
                self.assertNotIn('T000',set(result.con_code))
                self.assertIn('T100',set(result.con_code))
                self.assertEqual(set(result.eligibility_policy),{'month_end_non_ST_v1'})
                marker.assert_called_once_with(universe+'_membership_dates_ex_st_v1','20240131')

    def test_missing_truncated_or_wrong_date_evidence_blocks(self):
        cases=[pd.DataFrame(),pd.DataFrame({'ts_code':['A'],'trade_date':['20240201']}),
               pd.DataFrame({'ts_code':['A']*1000,'trade_date':['20240131']*1000})]
        for frame in cases:
            with self.subTest(rows=len(frame)):
                ns=functions();ns['tushare_call']=lambda *a:frame
                with self.assertRaises(ValueError):ns['historical_st_codes']('20240131')

    def test_provider_failure_is_actionable(self):
        ns=functions();ns['tushare_call']=Mock(side_effect=RuntimeError('provider unavailable'))
        with self.assertRaisesRegex(ValueError,'provider must support stock_st'):
            ns['historical_st_codes']('20240131')


if __name__=='__main__':unittest.main()

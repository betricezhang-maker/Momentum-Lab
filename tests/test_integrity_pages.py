"""Group pagination remains bounded when a dataset has many findings."""
import unittest
from momentumlab.integrity_pages import page


class IntegrityPagesTests(unittest.TestCase):
    def test_12000_groups_filter_then_sort_then_page(self):
        findings=[]
        for i in range(12001):
            findings.append(dict(issue_id=f'CMP-{i:016d}',ticker=f'T{i:05d}',date='20260105',
                end_date='20260105',session_dates=['20260105'],affected_component='raw_price',
                classification='UNEXPLAINED_MISSING',severity='FAIL',resolution_status='UNRESOLVED',
                blocks_research=True,overridable=True,raw_missing_dates=['20260105']))
        report={'completeness':{'findings':findings},'checks':[]}
        first=page(report,size=50,number=1)
        self.assertEqual(first['total_groups'],12001)
        self.assertEqual((first['from'],first['to'],len(first['groups'])),(1,50,50))
        last=page(report,size=50,number=999)
        self.assertEqual((last['page'],last['to'],len(last['groups'])),(241,12001,1))
        filtered=page(report,size=25,filters={'ticker':'T0000'},sort='ticker')
        self.assertEqual(filtered['total_groups'],10)
        self.assertEqual(filtered['total_findings'],10)


if __name__=='__main__':unittest.main()

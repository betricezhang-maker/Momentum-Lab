import copy
import unittest
from datetime import datetime
import pandas as pd
import test_bulk_review as fixtures
from momentumlab.integrity_notices import freshness_details, SHANGHAI
from momentumlab.issue_review import all_findings
from momentumlab.integrity_pages import page
from momentumlab.data_integrity import save_acknowledgement
from momentumlab.completeness import apply_issue_resolutions


class NoticeTests(unittest.TestCase):
    def test_freshness_respects_publication_and_future_calendar(self):
        cal=pd.DataFrame({'trade_date':['20260918','20260921','20260922','20260923'],'is_open':[1]*4})
        raw=pd.DataFrame({'trade_date':['20260921']})
        early=freshness_details(cal,raw,raw,now=datetime(2026,9,22,16,tzinfo=SHANGHAI))
        late=freshness_details(cal,raw,raw,now=datetime(2026,9,22,19,tzinfo=SHANGHAI))
        self.assertEqual((early['expected_session'],early['raw_session_lag']),('20260921',0))
        self.assertEqual((late['expected_session'],late['raw_session_lag']),('20260922',1))
        weekend=freshness_details(cal,raw,raw,now=datetime(2026,9,20,19,tzinfo=SHANGHAI))
        self.assertEqual(weekend['expected_session'],'20260918')

    def test_notices_blocker_display_and_no_wildcard_confirmation(self):
        f=fixtures.BulkReviewTests();f.setUp();self.addCleanup(f.doCleanups)
        report=f.f.report(mode='full');rows=all_findings(report)
        notices=[r for r in rows if r['ticker']=='*']
        self.assertTrue(notices)
        self.assertTrue(all(not r['repair_eligible'] and not r['blocks_research'] for r in notices))
        partial=next(r for r in notices if r['affected_component']=='partial_years')
        self.assertEqual(partial['severity'],'INFO')
        self.assertEqual(partial['scope_label'],'Research coverage')
        evidence=next(r for r in notices if r['classification']=='EVIDENCE_LIMITATION')
        self.assertIn('Suspension/status evidence unavailable',evidence['explanation'])
        forged=copy.deepcopy(evidence);forged['overridable']=True
        with self.assertRaisesRegex(ValueError,'Dataset-wide'):
            save_acknowledgement(f.paths,forged,'','Cannot apply globally')
        ack=dict(ticker='*',affected_component='raw_price',active=True,explanation='Wildcard',session_signatures={})
        resolved=apply_issue_resolutions(copy.deepcopy(report['completeness']),[ack])
        self.assertGreater(resolved['unresolved_blocking_count'],0)
        view=page(report)
        self.assertTrue(all(x['scope']=='A' for x in view['blocking_reasons']))
        self.assertTrue(all(not g['repair_issue_ids'] for g in view['groups'] if g['members'][0]['ticker']=='*'))
        raw=next(r for r in report['completeness']['findings'] if r['affected_component']=='raw_price')
        save_acknowledgement(f.paths,raw,'','Explicit ticker review',findings=report['completeness']['findings'])
        fresh=f.f.report(mode='full')
        self.assertTrue(fresh['research_allowed'])
        self.assertEqual(page(fresh)['blocking_reason_count'],0)

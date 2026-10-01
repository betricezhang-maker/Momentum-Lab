import unittest
import json
import threading
from urllib.request import Request, urlopen
from pathlib import Path
from unittest.mock import patch
import test_completeness as fixtures
from momentumlab import pending_builds as pending
from momentumlab.data_integrity import validate_dataset, save_acknowledgement, clear_integrity_cache


class PendingBuildTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CompletenessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.drop('raw_price','A',[16]);self.f.drop('adjusted_price','A',[16])
        self.state,self.work=pending.create(self.f.paths,'CSI300',{'end_date':str(self.f.days[-1].date())})
        self.state.update(download_complete=True,status='AWAITING_REVIEW')
        pending.write(self.state,self.work['root'])

    def confirm(self):
        report=validate_dataset(self.work,universe_id='CSI300',mode='full')
        raw=next(r for r in report['completeness']['findings'] if r['affected_component']=='raw_price')
        save_acknowledgement(self.work,raw,'','已核对，接受已知缺口','test evidence',findings=report['completeness']['findings'])

    def test_retained_review_restart_and_publish(self):
        baseline=pending.identities(pending.targets(self.f.paths))
        with self.assertRaisesRegex(ValueError,'INTEGRITY FAIL'):
            pending.publish(self.f.paths,'CSI300',self.state['id'])
        self.assertEqual(pending.identities(pending.targets(self.f.paths)),baseline)
        self.confirm();clear_integrity_cache()
        state,work=pending.load(self.f.paths,'CSI300',self.state['id'])
        self.assertEqual(state['status'],'AWAITING_REVIEW')
        self.assertTrue(validate_dataset(work,universe_id='CSI300')['research_allowed'])
        self.assertEqual(pending.identities(pending.targets(self.f.paths)),baseline)
        result=pending.publish(self.f.paths,'CSI300',self.state['id'])
        self.assertEqual(result['status'],'WARNING')
        report=self.f.report(mode='full')
        self.assertTrue(report['research_allowed'])
        self.assertEqual(report['completeness']['confirmed_count'],2)
        self.assertTrue(Path(work['raw_price_csv']).exists())
        with self.assertRaisesRegex(ValueError,'already been published'):
            pending.publish(self.f.paths,'CSI300',self.state['id'])

    def test_new_production_confirmation_blocks_overwrite(self):
        self.confirm()
        Path(self.f.paths['root'],'completeness_acknowledgments.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'changed after'):
            pending.publish(self.f.paths,'CSI300',self.state['id'])

    def test_publication_failure_retains_reviewed_candidate(self):
        self.confirm();before=pending.identities(pending.targets(self.f.paths))
        with patch.object(pending,'publish_dataset',side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError,'disk failure'):
                pending.publish(self.f.paths,'CSI300',self.state['id'])
        self.assertEqual(before,pending.identities(pending.targets(self.f.paths)))
        self.assertTrue(Path(self.work['raw_price_csv']).is_file())
        self.assertTrue(validate_dataset(self.work,universe_id='CSI300',mode='full')['research_allowed'])

    def test_incomplete_build_cannot_be_reviewed_or_published(self):
        self.state['download_complete']=False;pending.write(self.state,self.work['root'])
        with self.assertRaisesRegex(ValueError,'not ready'):
            pending.review_paths(self.f.paths,'CSI300',self.state['id'])
        with self.assertRaisesRegex(ValueError,'did not complete'):
            pending.publish(self.f.paths,'CSI300',self.state['id'])

    def test_http_export_upload_apply_then_explicit_publish(self):
        import MomentumLabV2 as app
        from test_bulk_review import BulkReviewTests
        before=pending.identities(pending.targets(self.f.paths))
        with patch.object(app,'load_config',return_value={'active_universe':'CSI300'}), \
             patch.object(app,'selected_universe_paths',return_value=self.f.paths), \
             patch.object(app,'job_snapshot',return_value={'status':'idle'}):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            base=f'http://127.0.0.1:{server.server_port}'
            def post(route,**body):
                data={'universe':'CSI300','build_id':self.state['id'],**body}
                with urlopen(Request(base+route,data=json.dumps(data).encode()),timeout=20) as response:
                    return json.load(response)
            def wait(started):
                for _ in range(200):
                    with urlopen(base+'/api/completeness/bulk-status') as response:job=json.load(response)['job']
                    if job['status']!='running':break
                    threading.Event().wait(.02)
                self.assertEqual(job['status'],'completed',job)
                self.assertEqual(job['id'],started['job']['id'])
                return job['result']
            export=wait(post('/api/completeness/export-job',scope='all'))
            import base64
            with urlopen(base+'/api/completeness/workbook?universe=CSI300&build_id='+self.state['id']+'&id='+export['export_id']) as response:
                exported={'workbook':base64.b64encode(response.read()).decode()}
            workbook=BulkReviewTests().edit(exported)
            preview=wait(post('/api/completeness/import-job',workbook=workbook,filename='reviewed.xlsx'))
            self.assertEqual(preview['counts']['confirm'],1)
            wait(post('/api/completeness/apply-review',preview_id=preview['preview_id']))
            self.assertEqual(before,pending.identities(pending.targets(self.f.paths)))
            wait(post('/api/completeness/check-job'))
            with urlopen(base+'/api/completeness/page?universe=CSI300&build_id='+self.state['id']) as response:
                self.assertTrue(json.load(response)['summary']['research_allowed'])
            result=wait(post('/api/pending-builds/publish',confirmation='PUBLISH'))
            self.assertEqual(result['status'],'WARNING')
            self.assertEqual(self.f.report(mode='full')['completeness']['confirmed_count'],2)


if __name__=='__main__':unittest.main()

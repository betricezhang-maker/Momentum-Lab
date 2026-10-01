"""Real HTTP endpoints and optional Chromium UI flow against a temporary dataset."""
import os
import json
import shutil
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
import test_completeness as fixtures

class BulkHTTPTests(unittest.TestCase):
    def test_export_preview_apply_over_real_http(self):
        import MomentumLabV2 as app
        from test_bulk_review import BulkReviewTests
        fixture=BulkReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        with patch.object(app,'load_config',return_value={'active_universe':'CSI300'}),patch.object(app,'selected_universe_paths',return_value=fixture.paths),patch.object(app,'job_snapshot',return_value={'status':'idle'}):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            base=f'http://127.0.0.1:{server.server_port}'
            def post(path,data):
                with urlopen(Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}),timeout=30) as response:return json.load(response)
            exported=post('/api/completeness/export-review',{'universe':'CSI300'})
            workbook=fixture.edit(exported)
            preview=post('/api/completeness/import-review',{'universe':'CSI300','workbook':workbook})
            self.assertEqual(preview['counts']['confirm'],1)
            started=post('/api/completeness/apply-review',{'universe':'CSI300','preview_id':preview['preview_id']});self.assertTrue(started['ok'])
            for _ in range(100):
                with urlopen(base+'/api/completeness/bulk-status') as response:job=json.load(response)['job']
                if job['status']!='running':break
                threading.Event().wait(.05)
            self.assertEqual(job['status'],'completed',job)
            report=fixture.f.report(mode='full')
            self.assertTrue(report['research_allowed'])
            self.assertEqual(report['completeness']['confirmed_count'],2)

    @unittest.skip('Superseded by full portable integrity-workspace browser test')
    def test_browser_roundtrip(self):
        import MomentumLabV2 as app
        from test_bulk_review import BulkReviewTests
        import pandas as pd
        fixture=BulkReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        class TestHandler(app.Handler):
            def do_GET(self):
                if self.path=='/test-review':
                    html='''<!doctype html><meta charset="utf-8"><title>Temporary integrity test</title>
<select id="researchUniverse"><option>CSI300</option></select><button onclick="runIntegrityCheck()">Run Integrity Check</button><div id="integrityDashboard"></div>
<script>const $=id=>document.getElementById(id);function escapeHtml(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')}</script>
<script src="/ui/integrity.js"></script><script src="/ui/issue-review.js"></script>'''.encode()
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html);return
                super().do_GET()
        with patch.object(app,'load_config',return_value={'active_universe':'CSI300'}),patch.object(app,'selected_universe_paths',return_value=fixture.paths),patch.object(app,'job_snapshot',return_value={'status':'idle'}),patch.object(app,'tushare_call',return_value=pd.DataFrame()):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),TestHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            import sys
            command=[shutil.which('node'),'tests/bulk_browser.cjs',f'http://127.0.0.1:{server.server_port}',str(fixture.f.root),sys.executable]
            completed=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=90)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            print(completed.stdout)

    @unittest.skipUnless(os.environ.get('MOMENTUM_BROWSER_TEST')=='1','Opt-in browser integration with local Edge')
    def test_integrity_workspace_browser(self):
        import MomentumLabV2 as app
        import pandas as pd
        from test_bulk_review import BulkReviewTests
        fixture=BulkReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        # Sixty additional independent internal gaps exercise real group pagination.
        for key in ('raw_price','adjusted_price','adj_factor'):
            path=Path(fixture.paths[key+'_csv']);frame=pd.read_csv(path)
            template=frame[frame.ts_code.eq('B')]
            additions=[]
            for i in range(60):
                one=template.copy();one['ts_code']=f'T{i:03d}';one=one[pd.to_datetime(one.trade_date).ne(fixture.f.days[16])]
                additions.append(one)
            pd.concat([frame,*additions],ignore_index=True).to_csv(path,index=False)
        path=Path(fixture.paths['weights_csv']);weights=pd.read_csv(path)
        additions=[weights[weights.con_code.eq('B')].assign(con_code=f'T{i:03d}') for i in range(60)]
        pd.concat([weights,*additions],ignore_index=True).to_csv(path,index=False)
        class TestHandler(app.Handler):
            def do_GET(self):
                if self.path=='/test-workspace':
                    html=app.UI_FILE.read_bytes()
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html);return
                super().do_GET()
        with patch.object(app,'load_config',return_value={'active_universe':'CSI300'}),patch.object(app,'selected_universe_paths',return_value=fixture.paths),patch.object(app,'job_snapshot',return_value={'status':'idle'}),patch.object(app,'tushare_call',return_value=pd.DataFrame()):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),TestHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            import sys
            command=[shutil.which('node'),'tests/workspace_browser.cjs',f'http://127.0.0.1:{server.server_port}',str(fixture.f.root),sys.executable]
            completed=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=180)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            print(completed.stdout)

if __name__=='__main__':unittest.main()

"""Manual browser smoke fixture; isolated data, no provider calls. Exits after 5 min."""
import threading
from unittest.mock import patch
import pandas as pd
import MomentumLabV2 as app
from test_pending_builds import PendingBuildTests

fixture=PendingBuildTests();fixture.setUp()

class Handler(app.Handler):
    def do_GET(self):
        if self.path=='/fixture':
            html='''<!doctype html><meta charset="utf-8"><title>Temporary retained-build test</title>
<link rel="stylesheet" href="/ui/integrity-workspace.css">
<h1>Temporary test dataset — no production data</h1><section id="manager">
<div class="card"><select id="dataUniverse"><option>CSI300</option></select></div>
<div class="card"><div id="integrityDashboard"></div></div></section>
<script>const $=id=>document.getElementById(id);function escapeHtml(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')}</script>
<script src="/ui/integrity-workspace.js"></script>'''.encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html);return
        super().do_GET()

try:
    with patch.object(app,'load_config',return_value={'active_universe':'CSI300'}), \
         patch.object(app,'selected_universe_paths',return_value=fixture.f.paths), \
         patch.object(app,'job_snapshot',return_value={'status':'idle'}), \
         patch.object(app,'tushare_call',return_value=pd.DataFrame()):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        print(f'http://127.0.0.1:{server.server_port}/fixture',flush=True)
        try:threading.Event().wait(300)
        finally:server.shutdown();server.server_close()
finally:fixture.doCleanups()

"""Manual browser acceptance server; only a temporary synthetic portfolio is written."""
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from test_live import LiveTests
from momentumlab.serialization import dumps

case=LiveTests();case.setUp()
pid=case.service.create(dict(name='DISPOSABLE Paper Apply Test',start_date='2024-01-02',capital=10000,status='PAPER'),case.source)['id']
root=Path(__file__).resolve().parents[1]
class Handler(BaseHTTPRequestHandler):
    def reply(self,body,kind='application/json'):
        data=body.encode();self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/ui/live-portfolio.js':return self.reply((root/'ui/live-portfolio.js').read_text(encoding='utf-8'),'text/javascript')
        self.reply('''<!doctype html><html><body><h1>Disposable paper rebalance acceptance test</h1><select id="liveSelector"><option value="'''+pid+'''">Test portfolio</option></select><p id="liveMessage"></p><div id="liveContent"></div><div id="liveAudit"></div><script>
        const $=id=>document.getElementById(id),escapeHtml=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),pct=x=>String(x??'—');
        const clearPagedTables=()=>{},universeLabel=x=>x,pagedTable=()=>'';
        async function api(url,body){const r=await fetch(url,{method:'POST',body:JSON.stringify(body)});const j=await r.json();if(j.error)throw Error(j.error);return j;}
        </script><script src="/ui/live-portfolio.js"></script><script>loadLiveDetail();</script></body></html>''','text/html; charset=utf-8')
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            if self.path=='/api/live/detail':result=case.service.detail(pid,refresh=body.get('refresh',False))
            elif self.path=='/api/live/paper-rebalance':result=case.service.apply_paper_rebalance(pid,body)
            else:raise ValueError('Unexpected endpoint')
            self.reply(dumps(result))
        except Exception as e:self.reply(dumps({'error':str(e)}))
print('Disposable browser test on http://127.0.0.1:8766',flush=True)
try:ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
finally:case.doCleanups()

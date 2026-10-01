"""Exercise only a copied portable app; never launches the development app.

Usage: python tools/validate_packaged_workflows.py <private-validation-folder>
The folder must have paths.json produced by prepare_packaging_validation.py.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

WORK = Path(sys.argv[1]).resolve()
PATHS = json.loads((WORK / 'paths.json').read_text(encoding='utf-8'))
ROOT = Path(PATHS['portable_copy']).resolve()
assert ROOT.is_relative_to(WORK) and ROOT != WORK, 'Only a private validation copy is permitted.'
REPORT = {'steps': [], 'path_without_python': None}
ENV = os.environ.copy()
ENV['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') + os.pathsep + os.environ['SystemRoot']
ENV.pop('PYTHONHOME', None); ENV.pop('PYTHONPATH', None)
assert shutil.which('python', path=ENV['PATH']) is None
REPORT['path_without_python'] = ENV['PATH']


def note(name, **details):
    REPORT['steps'].append(dict(name=name, status='PASS', **details))
    (WORK / 'exe-validation.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')
    print(name + ': PASS', flush=True)


def request(base, path, body=None, raw=False):
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json'} if body is not None else {})
    with urlopen(req, timeout=600) as response:
        value = response.read()
    if raw: return value
    result = json.loads(value)
    assert result.get('ok', True), result
    return result


def launch(root, browser=False):
    exe = root / 'MomentumLab.exe'
    process = subprocess.Popen([str(exe)] + ([] if browser else ['--no-browser']),
                               cwd=WORK, env=ENV, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            state = json.loads((root / 'logs' / 'instance.json').read_text())
            if state['pid'] != process.pid: raise ValueError('Waiting for the new instance')
            base = 'http://127.0.0.1:' + str(state['port'])
            health = request(base, '/api/health')
            assert Path(health['app_root']).resolve() == root
            assert Path(health['resource_root']).resolve() == root / '_internal'
            return process, base
        except (OSError, ValueError, AssertionError):
            if process.poll() is not None: raise RuntimeError('EXE exited during startup; inspect copied logs.')
            time.sleep(.2)
    raise RuntimeError('EXE startup timed out; inspect copied logs.')


def stop(root, process):
    subprocess.run([str(root / 'MomentumLab.exe'), '--quit'], env=ENV, cwd=WORK,
                   creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=15)
    process.wait(timeout=30)
    assert process.returncode == 0
    assert not (root / 'logs' / 'instance.json').exists()


def db_digest(root):
    with sqlite3.connect((root / 'live' / 'momentumlab.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()


def workflows(root, base, label):
    for asset in ('/', '/ui/strategy-grid.js', '/ui/live-portfolio.js', '/ui/integrity.js', '/ui/research-audit.js', '/ui/pagination.js'):
        assert request(base, asset, raw=True)
    cfg = request(base, '/api/config')
    assert Path(cfg['data_folder']).resolve() == root / 'data'
    assert Path(cfg['results_folder']).resolve() == root / 'results'
    for paths in cfg.get('universe_file_paths', {}).values():
        assert all(Path(p).resolve().is_relative_to(root) for p in paths.values() if p)
    assert request(base, '/api/status')['status']
    note(label + ' Data Manager and bundled UI', root=str(root))
    integrity = request(base, '/api/validate', {'universe': 'ETF_250M', 'mode': 'full'})
    assert integrity['status'] in ('PASS', 'WARNING'), integrity
    note(label + ' integrity', integrity_status=integrity['status'])
    selections = ['Top 5', 'Top 10', 'Top 20', 'Q4', 'Q5']
    grid = request(base, '/api/strategy-grid', dict(universe='ETF_250M', lookbacks=[10, 20],
                   rebalances=[10], selections=selections, years=[2026], include_full=False, trading_cost_bps=5))
    assert len(grid['rows']) == 10
    assert set(r['selection'] for r in grid['rows']) == set(selections)
    assert all(not r.get('error') and Path(r['history_file']).is_relative_to(root) and Path(r['history_file']).is_file() for r in grid['rows'])
    (WORK / (label + '-grid.json')).write_text(json.dumps(grid), encoding='utf-8')
    note(label + ' Strategy Grid', rows=len(grid['rows']), selections=selections)
    research = request(base, '/api/run', dict(universe='ETF_250M', lookback=10, display_spans=[10, 20],
                       selection='Top 10', rebalance_days=10, start_date='2026-01-01', end_date='2026-09-04', trading_cost_bps=5))
    assert research['rebalance_history'] and research['stats']
    for path in research['files'].values():
        assert Path(path).is_relative_to(root) and Path(path).is_file()
        assert request(base, '/file?' + urlencode({'path': path}), raw=True)
    (WORK / (label + '-research.json')).write_text(json.dumps(research), encoding='utf-8')
    note(label + ' Research Lab and charts', files=len(research['files']))
    audit = request(base, '/api/research-audit', dict(universe='ETF_250M', signal_date='2026-09-04',
                    lookback=10, ticker='159208.SZ', mode='Full Signal Audit Package'))
    assert not audit.get('blocked') and len(audit['top10_target']) == 10
    archive = Path(audit['export_path']); assert archive.is_relative_to(root)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None and 'audit_manifest.json' in package.namelist()
    assert request(base, '/file?' + urlencode({'path': str(archive)}), raw=True) == archive.read_bytes()
    (WORK / (label + '-audit.json')).write_text(json.dumps(audit), encoding='utf-8')
    note(label + ' Audit and ZIP export', ranking_rows=len(audit['ranking']))
    portfolios = request(base, '/api/live/portfolios?view=all')['portfolios']
    assert portfolios, 'The copied portfolio database is required for persistence validation.'
    detail = request(base, '/api/live/detail', {'id': portfolios[0]['id'], 'refresh': False})
    assert detail['portfolio']['id'] == portfolios[0]['id']
    exported = request(base, '/api/live/export', {'id': portfolios[0]['id']})
    assert all(Path(p).is_relative_to(root) and Path(p).is_file() for p in exported['files'].values())
    note(label + ' Live Portfolio and export', portfolios=len(portfolios))
    return {'grid': grid, 'audit_relative': archive.relative_to(root).as_posix(),
            'portfolio_ids': [p['id'] for p in portfolios]}


process = None
try:
    process, base = launch(ROOT)
    note('EXE starts without Python in PATH', base=base)
    duplicate = subprocess.run([str(ROOT / 'MomentumLab.exe'), '--no-browser'], env=ENV, cwd=WORK,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    assert duplicate.returncode == 0
    assert json.loads((ROOT / 'logs' / 'instance.json').read_text())['pid'] == process.pid
    note('Duplicate launch reuses original backend')
    saved = workflows(ROOT, base, 'initial')
    digest = db_digest(ROOT)
    stop(ROOT, process); process = None
    note('Clean launcher shutdown')
    process, base = launch(ROOT)
    assert db_digest(ROOT) == digest
    assert [p['id'] for p in request(base, '/api/live/portfolios?view=all')['portfolios']] == saved['portfolio_ids']
    note('Portfolio database persists after restart')
    stop(ROOT, process); process = None
    moved = WORK / 'relocated portable 中文' / 'MomentumLab_V4_Stable'
    assert not moved.exists() and moved.resolve().is_relative_to(WORK)
    shutil.copytree(ROOT, moved)
    # Remove the old TEST path from reach without deleting anything, so stale
    # metadata cannot accidentally pass validation by reading its former file.
    hidden = ROOT.with_name('original-test-copy-retained')
    assert ROOT.is_relative_to(WORK) and hidden.is_relative_to(WORK) and not hidden.exists()
    ROOT.rename(hidden)
    ROOT = moved
    process, base = launch(ROOT, browser=True)
    assert db_digest(ROOT) == digest
    assert (ROOT / saved['audit_relative']).is_file()
    assert request(base, '/file?' + urlencode({'path': str(ROOT / saved['audit_relative'])}), raw=True)
    row = next(r for r in saved['grid']['rows'] if r['lookback']==10 and r['selection']=='Top 10')
    history = ROOT / 'results' / 'backtests' / saved['grid']['run_id'] / Path(row['history_file']).name
    anchor = json.loads(history.read_text(encoding='utf-8'))[0]['signal_date']
    preview = request(base, '/api/live/preview', dict(run_id=saved['grid']['run_id'], lookback=10,
                      selection='Top 10', rebalance_days=10, start_date=anchor, end_date='2026-09-14', cost_bps=5))
    assert preview['signal_date'] == anchor and len(preview['holdings']) == 10
    note('Moved copy resolves prior Grid run, audit and portfolio', root=str(ROOT))
    workflows(ROOT, base, 'relocated')
    stop(ROOT, process); process = None
    note('Relocated copy shuts down cleanly')
    REPORT['status'] = 'PASS'
    (WORK / 'exe-validation.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')
except Exception as error:
    REPORT['status'] = 'FAIL'; REPORT['error'] = repr(error)
    (WORK / 'exe-validation.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')
    raise
finally:
    if process is not None and process.poll() is None:
        try: stop(ROOT, process)
        except Exception as error: print('Test instance still running: ' + str(error), flush=True)

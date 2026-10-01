"""Make private copies for packaging tests. Source data is read-only input."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def hashes():
    paths = []
    for name in ('data', 'results', 'live', 'config'):
        paths.extend(p for p in (ROOT / name).rglob('*') if p.is_file())
    result = {}
    for path in paths:
        with path.open('rb') as stream:
            result[str(path.relative_to(ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def copy_private_data(destination):
    for name in ('data', 'results', 'backups'):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, destination / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('performance') if name == 'results' else None)
    for name in ('live', 'config', 'logs', 'backups'):
        (destination / name).mkdir(exist_ok=True)
    db = ROOT / 'live' / 'momentumlab.sqlite3'
    if db.exists():
        source = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        target = sqlite3.connect(destination / 'live' / db.name)
        try: source.backup(target)
        finally: target.close(); source.close()
    settings = ROOT / 'config' / 'settings.json'
    cfg = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
    old_roots = [Path(cfg.get(k) or ROOT / d) for k, d in [('data_folder', 'data'), ('results_folder', 'results')]]
    def relocate(value):
        if not value: return value
        path = Path(value)
        if not path.is_absolute(): return value
        for old, folder in zip(old_roots, ('data', 'results')):
            try: return (Path(folder) / path.relative_to(old)).as_posix()
            except ValueError: pass
        try: return path.relative_to(ROOT).as_posix()
        except ValueError: raise ValueError('Validation cannot use external input: ' + value)
    for key in ('raw_price_csv', 'adjusted_price_csv', 'weights_csv', 'adj_factor_csv', 'trade_calendar_csv', 'price_limits_csv'):
        if cfg.get(key): cfg[key] = relocate(cfg[key])
    cfg['universe_file_paths'] = {u: {k: relocate(v) for k, v in p.items()}
                                  for u, p in cfg.get('universe_file_paths', {}).items()}
    cfg.update(data_folder='data', results_folder='results', tushare_token='', active_universe='ETF_250M')
    (destination / 'config' / 'settings.json').write_text(json.dumps(cfg, indent=2), encoding='utf-8')


def main():
    bundle = Path(sys.argv[1]).resolve()
    if not (bundle / 'MomentumLab.exe').is_file(): raise SystemExit('Pass the built portable folder.')
    workspace = ROOT / 'build' / ('validation-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / 'original-data-hashes.json').write_text(json.dumps(hashes(), indent=2), encoding='utf-8')
    source = workspace / 'source-copy'; source.mkdir()
    for name in ('momentumlab', 'ui', 'tests'):
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__'))
    for path in ROOT.glob('*.py'): shutil.copy2(path, source / path.name)
    copy_private_data(source)
    portable = workspace / 'portable-test' / 'MomentumLab_V4_Stable'
    shutil.copytree(bundle, portable)
    copy_private_data(portable)
    (workspace / 'paths.json').write_text(json.dumps({'source_copy': str(source), 'portable_copy': str(portable),
                                                   'clean_bundle': str(bundle)}, indent=2), encoding='utf-8')
    print(workspace, flush=True)


if __name__ == '__main__': main()

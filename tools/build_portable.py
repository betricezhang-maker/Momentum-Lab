"""Build to a new directory every time. Never remove or overwrite a release."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != 'win32' or platform.machine().upper() not in ('AMD64', 'X86_64'):
        raise SystemExit('Build on 64-bit Windows with CPython 3.14.7.')
    if sys.version_info[:3] != (3, 14, 7):
        raise SystemExit('This verified baseline uses CPython 3.14.7. Use that interpreter to preserve the runtime.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'dist' / stamp
    output.mkdir(parents=True, exist_ok=False)
    environment = ROOT / 'build' / 'packaging-venv'
    python = environment / 'Scripts' / 'python.exe'
    if not python.exists(): venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-packaging.lock')], check=True)
    subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(output), '--workpath', str(ROOT / 'build' / stamp),
                    str(ROOT / 'MomentumLab.spec')], cwd=ROOT, check=True)
    bundle = output / 'MomentumLab_V4_Stable'
    for name in ('data', 'results', 'live', 'logs', 'backups', 'config'):
        (bundle / name).mkdir(exist_ok=True)
    (bundle / 'config' / 'settings.json').write_text(json.dumps({
        'data_folder': 'data', 'results_folder': 'results', 'tushare_token': '',
        'active_universe': 'ETF_250M', 'universe_file_paths': {},
    }, indent=2), encoding='utf-8')
    for name in ('PACKAGING_README.md', 'README_V4.md', 'DATA_INTEGRITY.md',
                 'RESEARCH_AUDIT.md', 'PERFORMANCE_REPORT.md', 'requirements-packaging.lock'):
        shutil.copy2(ROOT / name, bundle / name)
    sources = [ROOT / 'MomentumLabV2.py', ROOT / 'portable_launcher.py', ROOT / 'MomentumLab.spec',
               ROOT / 'requirements-packaging.lock', ROOT / 'build_portable.bat', Path(__file__)]
    sources += sorted((ROOT / 'momentumlab').glob('*.py')) + sorted((ROOT / 'ui').glob('*'))
    manifest = {
        'built_at_utc': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
        'architecture': platform.machine(), 'mode': 'PyInstaller one-folder',
        'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sources if p.is_file()},
        'exe_sha256': hashlib.sha256((bundle / 'MomentumLab.exe').read_bytes()).hexdigest(),
        'dependencies': subprocess.check_output([str(python), '-m', 'pip', 'freeze'], text=True).splitlines(),
        'validation': 'Build completed; consult PACKAGING_VALIDATION.md for runtime validation.',
    }
    (bundle / 'BUILD_MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'BUILD COMPLETE: {bundle}', flush=True)
    print('No user data or credentials were included. Keep _internal beside MomentumLab.exe.')


if __name__ == '__main__': main()

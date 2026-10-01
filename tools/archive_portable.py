"""Archive a clean unsigned build plus matching source; never include private data."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    bundle = Path(sys.argv[1]).resolve()
    if not bundle.is_relative_to(ROOT / 'dist'): raise SystemExit('Pass a clean distribution under dist/.')
    manifest = json.loads((bundle / 'BUILD_MANIFEST.json').read_text(encoding='utf-8'))
    assert manifest['exe_sha256'] == digest(bundle / 'MomentumLab.exe')
    for name, expected in manifest['source_sha256'].items():
        assert digest(ROOT / name) == expected, 'Source changed since build: ' + name
    for name in ('data', 'results', 'live', 'logs', 'backups'):
        assert not any(p.is_file() for p in (bundle / name).rglob('*')), 'Not a clean bundle: ' + name
    settings = json.loads((bundle / 'config' / 'settings.json').read_text(encoding='utf-8'))
    assert not settings.get('tushare_token')
    assert {p.name for p in (bundle / 'config').iterdir()} == {'settings.json'}
    for name in ('PACKAGING_README.md', 'PACKAGING_VALIDATION.md'):
        shutil.copy2(ROOT / name, bundle / name)
    output = bundle.parent
    archive = output / 'MomentumLab_V4_Stable_Unsigned.zip'
    source_archive = output / 'MomentumLab_V4_Source.zip'
    assert not archive.exists() and not source_archive.exists(), 'Archives already exist; do not overwrite a release.'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(bundle.rglob('*')):
            package.write(path, (Path(bundle.name) / path.relative_to(bundle)).as_posix())
    sources = set(manifest['source_sha256'])
    sources.update(('PACKAGING_README.md', 'PACKAGING_VALIDATION.md', 'README_V4.md',
                    'DATA_INTEGRITY.md', 'RESEARCH_AUDIT.md', 'PERFORMANCE_REPORT.md',
                    'BUILD_WINDOWS_PORTABLE.bat', 'requirements.txt', '.gitignore'))
    for folder in ('tests', 'tools'):
        sources.update(p.relative_to(ROOT).as_posix() for p in (ROOT / folder).rglob('*')
                       if p.suffix in {'.py', '.cjs', '.json'} and '__pycache__' not in p.parts)
    source_manifest = {name: digest(ROOT / name) for name in sorted(sources)}
    with zipfile.ZipFile(source_archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name in sorted(sources): package.write(ROOT / name, 'MomentumLab_V4_Source/' + name)
        package.writestr('MomentumLab_V4_Source/SOURCE_SHA256.json', json.dumps(source_manifest, indent=2))
    for path in (archive, source_archive):
        with zipfile.ZipFile(path) as package: assert package.testzip() is None
    checksums = '\n'.join(digest(p) + '  ' + p.name for p in (archive, source_archive)) + '\n'
    (output / 'SHA256SUMS.txt').write_text(checksums, encoding='ascii')
    print(json.dumps({'unsigned_bundle': str(archive), 'bytes': archive.stat().st_size,
                      'source_snapshot': str(source_archive), 'source_files': len(sources),
                      'validation': 'Final EXE acceptance blocked by Windows Smart App Control'}, indent=2))


if __name__ == '__main__': main()

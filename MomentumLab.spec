# -*- mode: python ; coding: utf-8 -*-
# Static resources only. Never collect credentials, datasets, portfolios or outputs.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'portable_launcher.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'ui'), 'ui')],
    hiddenimports=['matplotlib.backends.backend_agg'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['Agg']}},
    runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MomentumLab',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MomentumLab_V4_Stable')

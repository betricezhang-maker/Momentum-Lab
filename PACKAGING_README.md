# Momentum Lab V4 portable Windows package

This is a self-contained **Windows x64, one-folder** distribution. The target
computer does not need Python. No installer, registry entries, AppData storage,
or administrator privileges are required. Use a local folder you can write to.

## Start and close

Double-click **MomentumLab.exe**. The launcher opens the browser after the local
backend is healthy. It uses `127.0.0.1:8765`, or the next free port through 8794.
Keep the small Momentum Lab launcher window open. **Quit**, or closing that
window, stops the backend cleanly. Closing a browser tab alone does not stop the
app. Finish or cancel active Data Manager jobs before quitting; the launcher
refuses to interrupt a running request or data job.

Launching the same portable folder twice opens the existing instance. Different
portable folders are independent and can use different ports. Instance locks
are Windows kernel handles and disappear after a process exits or crashes;
`logs/instance.json` alone is not a lock. Do not point two copies at shared data.

## Folder layout

```text
MomentumLab_V4_Stable/
    MomentumLab.exe
    _internal/          Python, DLLs, dependencies and bundled UI; keep intact
    data/               Prices, calendar, eligibility, manifests and integrity history
    results/            Research, Grid histories, charts, audit ZIPs and exports
    live/               momentumlab.sqlite3 (portfolios and audit trail)
    logs/               Logs, instance metadata and Matplotlib font cache
    backups/            Place for operator backups
    config/             settings.json and local settings
    BUILD_MANIFEST.json Source hashes, EXE hash and dependency versions
```

All six writable folders are created if missing. A fresh package has empty data
and no token: use Data Manager to import/download data, or copy existing folders
as described below. Missing datasets produce Data Manager validation messages;
they are not manufactured by the launcher. Invalid settings JSON, external
absolute data paths, unwritable folders or missing bundled files cause a startup
error. Details are in `logs/launcher-error.log` or `logs/momentumlab.log`.

The existing integrity engine also maintains dataset-specific backups inside
`data/<universe>/update_backups`; packaging does not relocate or alter those.

## Move your existing data or move to another PC

1. Stop all running Momentum Lab copies before copying a portfolio database.
2. Copy the existing `data`, `results`, `live`, `config`, `logs` and `backups`
   folders into a **new** portable app folder. Preserve the originals as backup.
3. In `config/settings.json`, prefer `"data_folder": "data"` and
   `"results_folder": "results"`. File overrides should be relative to the app
   root, such as `data/ETF_250M/calendar.csv`. Conventional legacy absolute paths
   under the old data/results roots relocate automatically. Custom external
   paths must first be copied into the portable folder and updated in settings.
4. Copy the **entire** app folder, including `_internal`, to the other drive/PC.
   Launch the EXE there. Do not copy only the EXE.

Writable paths are based on `sys.executable` in a frozen build, independent of the
working directory. Source mode uses the source file directory. Bundled HTML,
JS and CSS use PyInstaller's resource directory (`_MEIPASS`); persistent data
never uses it. Grid histories resolve inside their verified saved run folder.
Old audit records may retain original path strings as provenance; those are not
required storage locations. Do not edit historical audit records to rewrite them.

The clean build includes **no personal datasets, live portfolios or API tokens**.
A private validation copy may contain copied portfolios/data and must be treated
as private. Never share `config/settings.json` with credentials in it.

## Build from source

Use **64-bit Windows, CPython 3.14.7**, and an internet connection for the first
dependency download. This matches the verified source runtime. From the source
folder, run either:

```bat
build_portable.bat
build_portable.bat "C:\path\to\python.exe"
```

The build uses an isolated `build/packaging-venv`, exact versions in
`requirements-packaging.lock`, and `MomentumLab.spec`. The older
`BUILD_WINDOWS_PORTABLE.bat` forwards to this script. The general source
`requirements.txt` is not used because its pandas range predates this verified
runtime. No research, ranking, costs, portfolio or integrity formulas are changed.

Each build writes to a **new** directory:

```text
dist/<build-timestamp>/MomentumLab_V4_Stable/
```

Existing distributions and mutable data are never deleted or overwritten.
`BUILD_MANIFEST.json` records the source and runtime used. A successful build
alone is not a successful runtime test; see `PACKAGING_VALIDATION.md` for the
actual validation scope and any remaining limitations.

## Update without losing data

Quit the app and make a backup of the entire folder. Extract the new build into a
new folder, then copy your six writable folders into it. Test the new copy before
removing the old one. Alternatively, replace **both** `MomentumLab.exe` and the
whole `_internal` directory while the app is stopped. Never merge old/new runtime
DLLs, and never replace your existing `data`, `results`, `live`, `config`, `logs`
or `backups` with the empty folders from a clean release.

## Validation helpers

`MomentumLab.exe --no-browser` starts the same app without opening a browser.
`MomentumLab.exe --quit` requests a safe shutdown of the instance in that folder;
it does not force an active request/job to stop. These are local launcher controls,
not HTTP endpoints. The normal double-click path opens the browser automatically.

The binary is not code-signed. Windows Smart App Control **blocked the final
rebuilt EXE on the build PC**, so this artifact is not yet certified as a stable
release. This is an operating-system signing-policy block before Python starts,
not a completed runtime test. Keep Windows security enabled. A trusted publisher
signature is the appropriate next step; signing and a repeat of the EXE tests
are still required on this PC. Microsoft describes the requirements in its
[Smart App Control developer documentation](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview).

This package targets Windows x64; another operating system needs its own native
build. Test on the destination PC before relying on it for daily work.

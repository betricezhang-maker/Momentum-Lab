# V4 portable packaging validation — 2026-09-17

**Build created; final EXE acceptance is blocked by Windows Smart App Control.**
Do not interpret the requested `MomentumLab_V4_Stable` folder name as a completed
stable-release certification. No commit or push was made.

## Deliverable and build

Final unsigned one-folder output:
`dist/20260917-070826-378119/MomentumLab_V4_Stable/`.
The ZIP alongside that folder includes the entire runtime and empty portable
storage folders. It includes no personal dataset, portfolio database or token.

Python 3.14.7 x64, PyInstaller 6.22.3; pandas 3.0.5, numpy 2.5.2,
matplotlib 3.11.1 and requests 2.34.2. Runtime transitive dependencies are pinned
to the verified application environment in `requirements-packaging.lock`.
`BUILD_MANIFEST.json` records source hashes, the EXE hash and installed versions.

## Runtime paths inspected before building

| Concern | Behavior |
| --- | --- |
| Frozen executable root | Parent directory of `sys.executable` |
| Source root | Parent directory of `MomentumLabV2.py` |
| Bundled static UI | `resource_dir()` / `_MEIPASS/ui`, read-only resources |
| Data/results | Configuration resolved relative to executable/source root |
| Live database | `<root>/live/momentumlab.sqlite3` |
| Logs/config/font cache | External `logs/`, `config/`, `logs/matplotlib/` |
| Backups | External `backups/`; existing integrity backups also remain under `data/<universe>/update_backups/` |
| External configured paths | Portable launcher rejects them before application startup |

Packaging changes are limited to launcher lifecycle, build tooling, a shutdown
guard, and resolution of saved Grid histories inside their verified run folder.
The latter prevents copied metadata from depending on the former absolute path.
No research, momentum, ranking, costs, portfolio or integrity formulas changed.
Seven core calculation function ASTs matched the saved pre-packaging baseline.

## Results

| Check | Result and scope |
| --- | --- |
| Python regression suite | **PASS: 80 tests** in an isolated source/data copy, including HTTP Grid→Live→export and five packaging tests |
| JavaScript suites | **PASS:** UI, historical preview, research audit and pagination |
| Native runtime bundled | **PASS:** initial EXE loaded Python/Tcl/Tk DLLs from its own `_internal` directory |
| Data Manager UI | **PASS on initial build:** copied 890,937-row ETF prices and calendar loaded |
| Full integrity check | **PASS on initial build:** no blocking errors; existing `latest_prices` and `partial_years` warnings retained |
| Research Lab | **PASS on initial build:** MOM10, Top10, 10D; MOM10/20 comparison; all four chart images loaded |
| Strategy Grid | **PASS on initial build:** MOM10/20 × Top5/Top10/Top20/Q4/Q5; 10 result rows, no NameError |
| Live Portfolio | **PASS on initial build:** copied portfolio and permanent audit events loaded; scheduled dates September 18/21 visible |
| Audit/export | **PASS on initial build:** full signal audit generated; ZIP HTTP download matched saved ZIP bytes |
| Portfolio CSV export / safe quit | **PASS on initial build:** six export files generated; launcher quit event closed the backend and removed instance metadata |
| Final EXE with Python removed from PATH | **BLOCKED before startup:** Windows error 4551 |
| Final EXE duplicate launch/restart/persistence | **NOT COMPLETED:** depends on resolving the signing block |
| Relocated final EXE with spaces/Chinese path | **NOT COMPLETED:** depends on resolving the signing block |
| Source-mode relocation / relocated history lookup | **PASS** in unit tests; does not substitute for relocated EXE acceptance |
| Original user files | **PASS:** 3,551 original data/results/live/config files retained identical SHA-256 hashes |

The initial build is `dist/20260917-065742-947528/`; it ran in a private copy
with the same application/launcher code. The final build additionally pins and
bundles the verified optional `simplejson` and `typing_extensions` dependencies.
Initial-build observations are deliberately not reported as final-build tests.
The initial launch used a restricted PATH request, but a native Windows
PowerShell environment override can append system/user PATH entries; therefore
it is **not** counted as a proven Python-free-PATH test.

Three test harness issues were corrected without changing application math:
Python 3.14's AST dump omits empty fields by default; the hash test now explicitly
retains them. The V3 comparison function is copied verbatim from commit
`b662b15` into a test fixture, so testing a source copy does not require Git.
The historical ETF control now uses the already-verified September dataset's
Top10, checks its exact fingerprint and requires explicit opt-in for local data.

## Block evidence and next step

At local time 07:11:05, Code Integrity events **3033, 3077 and 3118** reported that
the final `MomentumLab.exe` failed the required signing policy. Process creation
raised **WinError 4551**, before the launcher or Python could run. No Windows
security policy was changed and no blocked executable was bypassed.

A certificate trusted for Windows code signing is needed to sign the final
artifact and repeat the pending tests on this PC. Smart App Control can also
evaluate bundled native dependencies; acceptance must be checked after signing.
See [Microsoft's guidance](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview).

The validation harness is ready:

```bat
python tools\prepare_packaging_validation.py "dist\<timestamp>\MomentumLab_V4_Stable"
python tools\validate_packaged_workflows.py "build\validation-<timestamp>"
```

It removes Python from the child process PATH, uses a different working
directory, tests duplicate startup, all main workflows, restart/persistence,
and copies the entire app to a path containing spaces and Chinese characters.
It renames only its original **test** copy to make stale paths unavailable.
The source dataset is never its runtime storage. Run the harness after signing.

Private test logs are under `build/validation-20260917-070104/` and
`build/validation-20260917-071009/`. Do not distribute these private copied
datasets or portfolio databases as part of the clean app package.

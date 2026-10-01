# Data integrity and provenance

The shared service is `momentumlab/data_integrity.py`. Application callers supply
paths from `selected_universe_paths`; no validation path is machine-specific.
`validate_dataset(paths, through=None, universe_id=..., mode='full'|'cached')`
returns PASS, WARNING or FAIL, individual checks, coverage, duplicate evidence,
a manifest and a SHA-256 fingerprint. Validation does not rewrite market files.
Audit events are appended to the universe's `integrity_audit.jsonl`.

`structural_status` is separate from `completeness.status`. The latter uses
`momentumlab/completeness.py`: dated membership plus prior lookback sessions,
independent raw/factor/adjusted key coverage, listing/delisting evidence, and
documented full-session suspensions. Unexplained required holes fail, including
shared holes and individually stale tickers. Unknown evidence warns. The default
scope ends at the latest raw date and uses 120 warm-up sessions. The saved local
calendar is authoritative; validation cannot discover errors in the calendar itself.

Optional universe-side evidence: `securities.csv` has `ts_code,list_date,delist_date`;
`suspensions.csv` has `ts_code,start_date,end_date,full_session,source` (1/true and
a nonempty source required). Classification listing dates are used when available.
Malformed evidence warns rather than explaining away gaps. Evidence files participate
in fingerprints and staging. No network requests or automatic history repair occur.

The integrity UI exports the full report as JSON, including compressed missing-date
ranges, component, classification, severity, evidence source and signal impact.
Confirmed suspension explains coverage but never permits a bridged MOM score.
The shared signal gate requires exactly N+1 prices across N exchange sessions;
complete-data arithmetic, ranking and execution conventions remain unchanged.
An incremental tail-download skip does not bypass staged completeness validation.

Schema, key, finite-price, calendar-session, factor-ratio and classification
failures block calculations. Missing optional returns/weights are allowed;
partial years and lagging valuations are warnings. Safe warnings permit use.
Price history for previously eligible securities is retained. A ticker absent
from all eligibility snapshots produces a warning, not automatic deletion.

ETF validation requires local `fund_classification.csv` evidence, generated
by builds/updates using the existing Tushare `fund_basic.fund_type` rule.
Checking integrity does not contact Tushare. Legacy datasets lacking this file
fail explicitly until classification evidence is obtained through an update.
Prohibited or unknown eligibility classifications fail rather than silently
filtering the input. No momentum, ranking, size threshold or cost formula changes.

Full builds, incremental updates and the adjusted-price rebuild endpoint stage
their writes before validation and publication. Incremental staging persists
across failure/resume. The publication transaction backs up current files, writes
a recovery marker, replaces validated components, and installs
`dataset_manifest.json` last. Readers in the application share a lock with
publication. Python failures restore backups; startup recovers a process-crash
interruption before serving requests. This is a recoverable multi-file local
transaction, not a filesystem-wide atomic rename or a distributed transaction.
Operate one application instance against a given dataset folder.

An old dataset is only known-good if validation established that fact. Backing up
an already corrupt legacy dataset does not make it valid. Failed staging never
turns such a dataset into PASS. Backups remain in `update_backups/`.

`repair_duplicate_keys(paths, universe_id)` is explicit: it rejects conflicting
values, logs affected keys, normalizes/sorts identical duplicates in staging,
revalidates, backs up production and publishes only a valid result. It is also
available via POST `/api/repair-safe`. No repair is run automatically by validation.
Refreshed input replaces matching old keys under the explicit upsert policy;
conflicting values within one refreshed batch or unrefreshed history fail.

Research metadata, Strategy Grid rows and JSON rebalance histories carry the
dataset fingerprint and run identity. Live saved signals carry their own dated
provenance and source strategy run ID. Previously saved results are never
relabeled with a later dataset ID. Reconstruction records the actual current
dataset it uses, even when its source strategy came from an older run.

Cache keys include file location, size, mtime, ctime, requested dates and the
publication marker. Manual checks and post-update validation force a full scan.
Repeated unchanged research gates reuse the result. Fingerprints exclude paths
and validation timestamps, so identical CSV contents retain identity after a move.

The Data Manager's Run Integrity Check is local-only. It reports component
coverage, keys, classification, warnings and expandable evidence. Strategy Grid
shows the same summary before running. Invalid Live input blocks model generation
before any saved actual holdings/signals are changed; existing account records
remain in SQLite and can be inspected once data integrity is restored.

Regression coverage includes duplicates and repair, idempotent upserts,
calendar/point-in-time rules, prohibited ETF classes, moved folders, rollback and
crash recovery, immutable provenance, Live holdings protection, and unchanged
calculation functions. All tests use synthetic temporary datasets except the
explicit local diagnostic run, which only reads financial CSVs.

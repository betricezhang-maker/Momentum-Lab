# Momentum Lab System Manual

For Excel review, bulk confirmation/reopening, and targeted batch repairs, see
[Bulk dataset-integrity review](BULK_ISSUE_REVIEW.md).

Momentum Lab is a local-first quantitative research and portfolio-recording
platform. It downloads and validates market data, calculates momentum signals,
simulates portfolios, compares strategy variations, and maintains an auditable
paper or manual portfolio ledger.

This document describes the current V4 implementation in this repository. It is
intended for both an operator using the browser application and a developer
maintaining the source. The source module is still named MomentumLabV2.py for
historical reasons; the runtime reports APP_VERSION = 4.0.0.

## 1. System boundary

Momentum Lab provides:

- local market-data import, full build, and incremental update;
- data-integrity checks, manifests, fingerprints, checkpoints, and backups;
- MOM research and forward-return diagnostics;
- Strategy Grid robustness comparisons;
- live schedule generation and a paper/manual portfolio ledger;
- research and portfolio audit exports.

Momentum Lab does not:

- connect to a broker;
- submit orders;
- guarantee an executable market fill;
- turn a missing price row into a valid observation;
- infer an exact saved research result from a nearby date.

The application is a local HTTP server bound to 127.0.0.1. The browser is only
the view layer; calculation and ledger state remain on the local machine.

## 2. Architecture

The major execution path is:

    Browser UI
        |
        | same-origin JSON requests
        v
    MomentumLabV2.py / Handler
        |
        +--> data job worker --> Tushare/proxy --> staged CSV files
        |                           |
        |                           +--> integrity validation
        |                           +--> manifest/fingerprint
        |                           +--> backup + publication
        |
        +--> research engine --> ranked DataFrame --> backtest/grid results
        |
        +--> LiveService --> live_model/rebalance_workflow/live_valuation
                                    |
                                    +--> live/momentumlab.sqlite3
                                    +--> result CSV/JSON exports

Important modules:

| Module | Responsibility |
| --- | --- |
| MomentumLabV2.py | Startup, configuration, data jobs, Tushare calls, research engine, HTTP API |
| momentumlab/data_integrity.py | Key normalization, duplicate checks, schema/price/calendar/factor validation, staging and publication |
| momentumlab/turnover.py | Securities-only one-way turnover and summaries |
| momentumlab/costs.py | Gross-to-net turnover cost application |
| momentumlab/live_model.py | Dated signal selection, schedules, target snapshots |
| momentumlab/rebalance_workflow.py | Proposed trade rows and workflow status |
| momentumlab/live_valuation.py | Manual ledger, raw-close valuation, adjusted model NAV |
| momentumlab/live_service.py | Portfolio creation, signals, paper/actual rebalances, cash flows, status |
| momentumlab/live_store.py | SQLite schema and transactional record storage |
| momentumlab/research_audit.py | Exact-date MOM traces and target consistency comparisons |
| momentumlab/reconstruction.py | Historical target reconstruction and saved-history matching |
| ui/index.html | Single-page shell, controls, dialogs, page labels |
| ui/strategy-grid.js | Strategy Grid rendering, history and Live Portfolio promotion |
| ui/live-portfolio.js | Live Portfolio rendering and API actions |
| ui/research-audit.js | Research Audit rendering and export controls |
| portable_launcher.py | Frozen Windows launcher, portable paths, instance lock, shutdown |

### End-to-end wiring

| User action | Browser request | Backend path | Durable result |
| --- | --- | --- | --- |
| Run Integrity Check | POST /api/validate | validate_research_files → validate_dataset | Checks, manifest, fingerprint, integrity audit event |
| Build dataset | POST /api/full-build | start_data_job → full_build worker | Validated CSVs, backup, dataset_manifest.json |
| Incremental update | POST /api/refresh | start_data_job → refresh_data worker | Staged upserts, adjusted rebuild, validated publication |
| Run Research Lab | POST /api/run | run_research → calculate_signal → backtest_v4 | results/backtests/<run-id> |
| Run Strategy Grid | POST /api/strategy-grid | run_strategy_grid → repeated backtest_v4 | Grid rows, histories, charts and metadata |
| Add to Live Portfolio | POST /api/live/create | verified_strategy_source → LiveService.create | SQLite portfolio and initial signal |
| Refresh Live Portfolio | POST /api/live/detail | LiveService.detail → target_snapshot/build_nav | Signals, NAV snapshot and workflow proposal |
| Preview capital flow | POST /api/live/rebalance-preview | LiveService.preview_rebalance | In-memory proposal with adjusted investable NAV |
| Apply PAPER rebalance | POST /api/live/paper-rebalance | LiveService.apply_paper_rebalance | SQLite rebalance, cash flow, event and paper holdings |
| Record ACTIVE fills | POST /api/live/actual-rebalance | LiveService.record_actual_rebalance | SQLite trades, cash flow, rebalance and event |
| Export audit | POST /api/live/export or /api/research-audit | export_portfolio or build_audit/export_audit | CSV/JSON or immutable ZIP under results |

## 3. Runtime and storage

### Source mode

From the repository directory:

    python -m pip install -r requirements.txt
    python MomentumLabV2.py

The application normally uses http://127.0.0.1:8765/. If that port is busy it
tries the next free port through 8794 and prints or opens the selected address.

RUN_DEVELOPMENT.bat starts the source application. RUN_DIAGNOSTIC.bat keeps a
console visible and prefers MomentumLabV2.exe when present.

### Portable Windows mode

The one-folder build contains:

    MomentumLab_V4_Stable/
        MomentumLab.exe
        _internal/
        data/
        results/
        live/
        logs/
        backups/
        config/

Persistent data is resolved relative to the executable directory. Bundled UI
assets may be under the PyInstaller resource directory, but _MEIPASS is never
used as persistent storage. Keep _internal beside the executable.

The launcher:

- creates and write-tests the six mutable folders;
- prevents duplicate instances of the same portable folder with a Windows mutex;
- writes logs/instance.json while the server is alive;
- starts the local backend and opens the browser;
- refuses to quit while a request or data job is active;
- stores startup errors in logs/launcher-error.log.

See PACKAGING_README.md and UNSIGNED_EXE_WINDOWS_GUIDE.md for packaging and
Windows Smart App Control procedures.

### Folder roles

| Folder | Contents | Operational rule |
| --- | --- | --- |
| data/<universe>/ | Calendar, prices, factors, adjusted prices, eligibility, classification, limits, manifest, integrity audit | Mutable market dataset; keep backups |
| results/backtests/<run-id>/ | Grid/research metadata, histories, equity/drawdown CSVs, charts, summaries | Required for reproducible saved research and exact target matching |
| results/reports/ and results/heatmaps/ | Research artifacts and charts | Safe to regenerate, but retain results used for audit |
| live/momentumlab.sqlite3 | Portfolios, signals, trades, rebalances, cash flows, NAV snapshots, events | Back up only while the app is stopped |
| config/settings.json | Relative storage paths, active universe, API settings, token | Treat as secret-bearing; never publish the token |
| logs/ | Launcher/app logs, job error/checkpoint diagnostics, Matplotlib cache | Inspect first when startup or worker state is unclear |
| backups/ | Operator backups; dataset update backups live under the dataset root | Do not replace with empty folders during an upgrade |

The repository .gitignore excludes runtime data, results, logs, live state and
secrets. Source code, tests and documentation belong in version control.

## 4. User interface map

### Dashboard

Shows dataset status and the research definition:

    MOM(N) = N-observation adjusted-price return
             / [sample daily-return SD × sqrt(N)]

It also describes the execution layers. Research uses a next-session adjusted
close proxy. Price-limit data is downloaded for future execution-policy work but
is not currently applied to the research-mode backtest.

### Data Manager

The Data Manager has four operational areas:

1. Locate Existing Local Data — choose CSV files or import a CSV.
2. Build / Rebuild Dataset — run a staged full build.
3. Incremental Update — extend an existing dataset through a selected date.
4. Data Integrity — run a local read-only validation or repair duplicate keys
   explicitly.

The job panel exposes stage, progress, elapsed time, worker state, recent API
requests, checkpoint state, errors and traceback. A dead worker is reported as
an error; the UI must not leave a false RUNNING state.

### Research Lab

Research Lab runs one primary momentum span and optional comparison spans. It
produces:

- signal-decay forward-return tables and heatmaps;
- lookback comparison tables;
- net equity and drawdown charts;
- rebalance history with KEEP / SELL / BUY details;
- a latest Top20 multi-span monitor;
- past ten-observation price-trend charts;
- saved result metadata and provenance.

Forward-return heatmaps are diagnostic averages after a signal. They are not
portfolio backtest returns.

### Strategy Grid

Strategy Grid tests combinations of:

- MOM lookback: 10, 20, 40, 60, 120;
- rebalance interval: 5D, 10D, 20D, 40D, 60D;
- selection: Top5, Top10, Top20, Q1–Q5;
- optional calendar years and Full Period;
- one-way trading-cost rate.

The primary table shows annual returns and coverage. Additional columns include
full return, CAGR/annualized volatility, drawdown, turnover and retention.
Heatmap colors are display-only:

- annual returns use a diverging scale centered at zero;
- positive-year reliability distinguishes high, mixed and low coverage;
- drawdown magnitude uses green/amber/red risk bands;
- Quality / 100 is a descriptive heuristic, not a probability or recommendation.

The quality score is:

    50% × positive_years / requested_years
    + 25% × clamp((full_CAGR / annualized_volatility) / 2)
    + 25% × clamp(1 − abs(full_max_drawdown) / 50%)

The result is multiplied by 100 and rounded. It is shown only when at least two
valid years, complete requested-year coverage and Full Period metrics exist.
It does not change calculations, row ordering, CSVs or API values.

The History action opens the saved rebalance history. Add to Live Portfolio
creates a portfolio from a verified successful row; it never updates an
existing portfolio.

### Live Portfolio

The Live Portfolio page contains:

- portfolio selector and active/closed/archived views;
- current NAV, cash, investment P/L and external-flow statistics;
- last/next signal and rebalance dates;
- current positions versus model targets;
- NEXT REBALANCE workflow and proposed trades;
- final target-only portfolio;
- rebalance history, model history, position history and capital-flow ledger;
- permanent events and export links.

Use Refresh & Save Snapshot to generate due saved signals and NAV history.
Automatic PAPER portfolios follow model targets. ACTIVE portfolios require
confirmed manual trades.

### Audit / Export

This page has two independent audit paths:

- Portfolio Audit Trail exports the selected portfolio's CSVs and JSON audit.
- Research / MOM Calculation Audit traces the production MOM columns for an
  exact open signal date and compares the production target against backtest,
  Research Lab and Strategy Grid evidence.

UNAVAILABLE means that exact saved target or execution evidence was not found.
It does not automatically mean that the current target calculation failed.

### Settings

Settings control:

- data and results roots;
- Tushare token and proxy URL;
- request rate, retries and cooldown;
- active universe;
- per-universe file paths;
- ETF capitalization threshold.

Clear Runtime Cache releases temporary DataFrames only. It does not remove
datasets, results, audit packages or portfolio records.

## 5. Supported universes

| Key | Eligibility source | Notes |
| --- | --- | --- |
| CSI300 | Dated index weights | Point-in-time membership |
| CHINEXT_TOP100 | Dated generated top-100 membership | Independent dataset |
| STAR_TOP100 | Dated generated top-100 membership | Independent dataset |
| ETF_250M | Dated ETF eligibility plus classification | Equity ETFs with historical capitalization at least RMB 250M |

ETF research requires fund_classification.csv evidence. Bond, cash/money market,
commodity and gold funds are excluded. Unknown or prohibited classification
does not silently pass.

Each universe has independent calendar, eligibility, raw, factor, adjusted and
optional limits files. Rankings are never mixed between universes.

## 6. Data contracts

The validator normalizes security keys to uppercase and dates to YYYYMMDD.
Daily primary keys are (ts_code, trade_date); eligibility uses
(con_code, trade_date).

### Trading calendar

Required columns:

    trade_date,is_open

is_open must be numeric 0 or 1. The calendar must be sorted, have unique dates,
and cover the requested-through date.

### Raw prices

Required columns:

    ts_code,trade_date,open,high,low,close

The full downloaded schema may also contain pre_close, change, pct_chg, vol and
amount.

### Adjustment factors

Required columns:

    ts_code,trade_date,adj_factor

### Adjusted prices

Required columns:

    ts_code,trade_date,adj_open,adj_high,adj_low,adj_close

Adjusted OHLC is rebuilt from raw OHLC and factors using the existing factor-ratio
methodology:

    adjusted_value = raw_value × factor_on_day / latest_factor_for_ticker

### Eligibility / membership

Index-style datasets use:

    con_code,trade_date,weight

ETF eligibility also requires market_cap_rmb (and retains source size fields).
The latest snapshot on or before a signal date is used; a future snapshot is
never used for a historical signal.

### Classification and limits

ETF classification requires ts_code, name and fund_type. Non-ETF limits use the
Tushare stk_limit fields where available.

## 7. Data pipeline

### Full build

The full build stages files in a temporary dataset workspace and publishes only
after validation:

1. Trading calendar — download open/closed sessions.
2. Eligibility — retrieve index weights, generate top-100 membership, or build
   ETF eligibility.
3. Raw prices — download daily OHLC history.
4. Adjustment factors — download factors.
5. Price limits — download for non-ETF universes when selected.
6. Adjusted prices — rebuild the complete adjusted file.
7. Integrity validation and publication — validate, manifest, back up and
   replace the production files.

### Incremental update

The incremental path preserves successful stages and uses the trading calendar
to compute missing open sessions before downloading:

1. Refresh and validate the calendar.
2. Refresh and validate point-in-time eligibility.
3. Compute missing open sessions and download ETF raw prices in one date batch
   per missing session.
4. Download adjustment factors in the same date-oriented manner where supported.
5. Rebuild adjusted prices using the existing full methodology.
6. Validate, create the manifest/fingerprint and publish.

Rows are filtered to the eligible universe and upserted by ts_code + trade_date.
The update panel reports missing sessions, eligible tickers, expected API calls,
rows fetched, rows written, tickers represented and duplicate keys after upsert.

If there are no missing open sessions, raw-price/factor requests are skipped and
the stage is labeled SKIPPED / ALREADY CURRENT. Adjusted prices are not
published until the raw/factor stages and integrity validation succeed.

### Worker, checkpoint and publication behavior

Data jobs run in a background worker. Checkpoints and staged files survive a
recoverable interruption. A worker exception persists:

- stage name;
- status and message;
- Python exception type and message;
- full traceback;
- stage results and request;
- whether publication committed.

If the worker exits without a traceback, the next status read creates a Stale
job error. Use Resume when staged checkpoints are valid, or Reset Stale Job when
the staged job must be discarded safely. Production files stay unchanged on a
failed pre-publication job.

Only one application instance should operate on a given dataset folder.

## 8. Integrity, provenance and known scope

validate_dataset() returns PASS, WARNING or FAIL with individual checks, component
coverage, duplicate diagnostics, a manifest and a SHA-256 fingerprint. It is
local-only and does not contact Tushare.

Blocking checks include:

- missing files or required columns;
- invalid keys, dates, finite/positive numeric values;
- duplicate daily keys;
- calendar dates not marked open;
- raw/adjusted key-set mismatch;
- missing factor coverage;
- adjusted OHLC not matching the factor-ratio formula;
- prohibited or unknown ETF classifications.

Warnings include partial research years, a globally lagging latest valuation date,
a price ticker absent from all eligibility snapshots, and short signal warm-up.

Structural integrity and data completeness are reported separately. Completeness
uses the saved exchange calendar and dated eligibility intervals, including prior
warm-up (120 sessions by default). It checks raw prices, adjusted prices and factors
independently, so a hole shared by all three files is still detected. An unexplained
required observation after documented listing or first observed quote is FAIL.
Unknown earlier history and unavailable lifecycle/status evidence produce WARNING.

Optional local evidence files in the universe folder are `securities.csv`
(`ts_code,list_date,delist_date`) and `suspensions.csv`
(`ts_code,start_date,end_date,full_session,source`). Classification metadata listing
dates are also used; the dedicated securities file takes precedence. Dates use
YYYYMMDD or YYYY-MM-DD. Only sourced, full-session suspensions explain absent raw
quotes; missing rows alone never establish a suspension. Pre-listing/post-delisting
dates are explained exclusions. Even a confirmed suspension invalidates a MOM
window crossing that session. Evidence files must come from trusted records.

MOM requires N+1 positive finite prices on exactly N consecutive open exchange
sessions ending on the signal date. No fill or bridging is allowed. The shared
gate applies to Research Lab, Grid, live targets and audit. Incomplete tickers have
no valid score; live targets cannot silently fall back to an older valid ranking.
Research exports `signal_exclusions.csv`; Grid exports one exclusion CSV per MOM span.

Run **Data Manager → Run Integrity Check** for the selected ETF universe. Inspect
the two statuses and representative findings, then click **Export full completeness
evidence (JSON)** for every ticker/date range, component, severity, evidence source
and signal impact. Counts of missing sessions are per component, not unique
ticker-days. The check downloads nothing and does not repair market files.

The default scope ends at the latest raw dataset date, not today; a globally stale
dataset remains subject to the separate freshness warning. The local calendar is
authoritative and is not externally verified. Missing lifecycle/suspension evidence
prevents a conclusive completeness PASS. Incremental SKIPPED / ALREADY CURRENT
only means no new tail sessions need downloading: historical completeness is still
validated before publication, and unresolved required holes block publication.
No automatic historical repair or full rebuild is initiated.

Published datasets include dataset_manifest.json, file hashes, component dates,
schema/version and staged results. Research runs, saved signals and audit
packages retain the fingerprint and run ID that produced them. Later data
updates do not relabel old results.

## 9. Research calculation fundamentals

Completeness review: use **Acknowledge with reason** on an overridable finding and
enter your explanation plus an optional reference. Its status becomes **CONFIRMED**:
manually reviewed and accepted, with the original missing-data classification retained.
It does not assert suspension or repair. Matching adjusted-price gaps inherit the
raw-price confirmation only on dates with verified absent raw prices. The UI shows
the explanation and parent link; adjustment-factor errors never inherit this review.
Only overlapping dates are accepted. New dates and changed relevant evidence need
review again. **Reopen** revokes the raw confirmation and its inherited confirmations.
Restored real observations display **RESOLVED** in retained confirmation history.

Confirmed completeness exceptions permit Research Lab and Strategy Grid with a
visible WARNING if no other blockers remain. Structural errors remain blocking.
Incomplete MOM windows are still excluded. The full JSON evidence and saved run
metadata retain confirmations; later reviews do not modify old run evidence.

### MOM score

For lookback N, for ticker t on signal date d:

    return_N(t,d) = adjusted_close(t,d) / adjusted_close(t,d-N) − 1

    daily_return_i = adjusted_close_i / adjusted_close_(i-1) − 1

    volatility_N = sample_SD(last N daily_return_i) × sqrt(N)

    MOM_N = return_N / volatility_N

The window uses N+1 prices and N returns. Missing values are not filled for the
MOM calculation. The sample standard deviation uses ddof=1 and min_periods=N.
Infinite or undefined scores are excluded from ranking.

### Ranking and selection

On each exact signal date:

- apply the point-in-time eligibility snapshot;
- keep valid MOM scores;
- rank descending with production row order as the tie-break;
- select Top5/Top10/Top20/Top30/Top50 or a quintile;
- assign equal weights across the actual selected count.

Q5 is the strongest momentum quintile in the UI's signal-decay labeling. A
quintile needs at least five valid scores.

### Forward-return diagnostics

Research Lab's signal-decay and lookback-comparison tables map each signal date
to future trading sessions and average the selected securities' forward returns.
They do not maintain a portfolio, apply a rebalance schedule or charge turnover
costs. Their captions deliberately say signal-close forward returns.

## 10. Backtest engine

For a strategy such as MOM10 / Top10 / 10D:

1. Calculate MOM10 across the full available history so the requested range has
   warm-up observations.
2. Start on the first valid ranking date within the requested range.
3. Generate a signal every 10 open trading sessions.
4. Select the Top10 on the signal date.
5. Enter on the next exchange session using that session's adjusted close as the
   model execution-price proxy.
6. Mark holdings daily with adjusted close and retain cash.
7. At each execution, compare current weights to the new equal-weight target.
8. Deduct modeled cost if requested and replace quantities to target weights.

The pre-trade portfolio value is the execution-date marked value of cash and
existing shares. With zero cost, each of ten targets receives before / 10. With
cost, the engine allocates after_cost / 10.

Securities-only one-way turnover is:

    0.5 × sum(abs(target_weight − current_weight))

Initial deployment from cash is 100% one-way turnover in the backtest's
rebalance log. A completely replaced invested Top10 is also 100%. Two exact
10%-weight replacements are approximately 20%.

The gross NAV is normalized to the first recorded NAV:

    gross_total_return = final_normalized_NAV − 1

With a rate r = cost_bps / 10000, each rebalance applies:

    cost_drag = one_way_turnover × r
    net_NAV_after = net_NAV_before × (1 − cost_drag)

Initial funding cost is included in net NAV; initial funding is excluded from
recurring turnover averages. Annualized return uses 252 observations. Volatility
is the standard deviation of daily returns times sqrt(252). Drawdown is measured
from the running equity peak.

The adjusted-close proxy is adequate for consistent historical comparison, but it
is not a claim about actual open prices, spreads, slippage, liquidity or fills.

## 11. Strategy Grid and saved research

Each Grid request writes a run directory under results/backtests containing:

    run_metadata.json
    summary.csv
    equity.csv
    drawdown.csv
    rebalance_log.csv
    rebalance_history.json
    signal/lookback tables and charts

The metadata records universe, strategy parameters, date ranges, integrity
status, dataset fingerprint, run ID, cost convention and source paths.

Grid rows can fail individually while other combinations complete. A failed row
must not be promoted to Live Portfolio. The promotion endpoint verifies that
the run ID exists under the configured results root and that the selected row
was successful.

## 12. Live Portfolio model

### Create

Use Add to Live Portfolio on a successful Strategy Grid row. Enter a name, PAPER
or ACTIVE mode, starting date/capital and cost rate. The portfolio stores:

- strategy parameters;
- verified source run ID;
- schedule anchor;
- starting capital;
- mode and status;
- provenance and dataset fingerprint.

Clicking the button again creates another independent portfolio; it does not
refresh or overwrite the first one.

### Scheduling

The schedule is based on the saved signal anchor and the shared open-session
calendar. A scheduled signal date can be known before its target is generated.
The next execution date is the next open session after the signal date.

Typical workflow statuses:

    UPCOMING
    SIGNAL_READY
    WAITING_FOR_EXECUTION
    PARTIALLY_EXECUTED
    COMPLETED
    SKIPPED

Apply Paper Rebalance and capital controls are enabled only when the current
signal has a generated target and the execution date is due.

### PAPER mode

PAPER portfolios automatically calculate model holdings, paper NAV, simulated
trades, model turnover, estimated cost, position history and rebalance history.
Manual trade entry is rejected.

For external capital:

1. Choose Add Capital or Withdraw Capital in NEXT REBALANCE.
2. Enter the RMB amount.
3. Select Update Rebalance Preview.
4. Review investable NAV, target values, proposed trades and expected cash.
5. Select Apply Paper Rebalance.

The ledger stores a CONTRIBUTION or WITHDRAWAL linked to that signal. A
contribution increases investable NAV but does not change target weights. A
withdrawal reduces investable NAV and must leave a positive investable balance.

### ACTIVE mode

ACTIVE holdings change only through confirmed manual records. Review / Record
Actual Rebalance pre-fills required BUY/SELL differences. Enter actual fills,
dates, quantities, prices and fees. Partial execution keeps the model target and
records tracking difference.

The manual ledger supports:

- BUY and SELL;
- DIVIDEND as total cash received;
- SPLIT as a share multiplier;
- correction by appending a superseding record;
- capital contributions and withdrawals;
- explicit journal completion.

Borrowing and overselling are rejected. Raw close prices value actual holdings;
adjusted prices value the model/PAPER path. A missing raw quote is labeled and
falls back to cost basis for actual valuation.

### Promotion and lifecycle

Continue as Active carries Paper NAV, cash, quantities, cost basis, schedule
anchor and history into a linked ACTIVE portfolio. PAUSED suppresses new
signals. CLOSED stops tracking but retains history. ARCHIVED hides a portfolio
from the default selector and can be restored. Permanent deletion requires
typing DELETE and is scoped to that portfolio ID.

## 13. Audit and export

### Research Audit

Choose an exact open signal date, universe, MOM lookback and audit mode:

- Single Ticker Calculation;
- Top Ranking Audit;
- Full Signal Audit Package.

The audit records exact adjusted-price inputs, daily returns, MOM values, ranking,
eligibility evidence, Top10 target, execution date and comparisons against:

- production target preview;
- production backtest;
- saved Research Lab history;
- saved Strategy Grid history.

Comparisons require exact date and parameter evidence. UNAVAILABLE means no exact
saved evidence was found; it is not silently inferred from a nearby run. The
export is a portable ZIP under the results root.

### Portfolio export

Portfolio export contains summary, positions, trades, rebalances, NAV history and
the permanent audit/event trail. Export before deleting or archiving a portfolio
if it will be used in external records.

## 14. Local demos

The following examples use the local server and placeholders. They do not
contain or require a real Tushare token in the documentation.

### Demo A: start and health check

Source mode:

    python MomentumLabV2.py

PowerShell:

    Invoke-RestMethod http://127.0.0.1:8765/api/health
    Invoke-RestMethod http://127.0.0.1:8765/api/status
    Invoke-RestMethod http://127.0.0.1:8765/api/universes

Expected health fields include ok, version, ui_exists, app_root and resource_root.

### Demo B: run local integrity validation

    $body = @{ universe = "ETF_250M"; mode = "full" } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/validate -ContentType "application/json" -Body $body

Inspect status, errors, warnings, checks, manifest and components. This endpoint
reads local files; it does not download or repair data.

### Demo C: run one Research Lab calculation

    $body = @{
      universe = "ETF_250M"
      lookback = 10
      display_spans = @(10,20,40)
      selection = "Top 10"
      rebalance_days = 10
      trading_cost_bps = 5
      start_date = "2024-01-01"
      end_date = "2026-09-17"
    } | ConvertTo-Json
    $research = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/run -ContentType "application/json" -Body $body
    $research.stats
    $research.files

The returned files.folder is the saved run directory. Do not delete it if you
need exact audit matching or Live Portfolio reconstruction.

### Demo D: run a small Strategy Grid

    $body = @{
      universe = "ETF_250M"
      lookbacks = @(10)
      rebalances = @(10)
      selections = @("Top 10")
      years = @(2025,2026)
      include_full = $true
      trading_cost_bps = 5
    } | ConvertTo-Json
    $grid = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/strategy-grid -ContentType "application/json" -Body $body
    $grid.run_id
    $grid.rows | Select-Object period,lookback,selection,rebalance_days,net_return,error

Use the browser Strategy Grid History action or the returned history_file to
inspect KEEP/SELL/BUY and turnover.

### Demo E: create a PAPER portfolio from a verified run

Prefer the browser's Add to Live Portfolio button because it supplies the
verified run ID and row parameters. The equivalent API shape is:

    $body = @{
      run_id = "<RUN_ID>"
      lookback = 10
      selection = "Top 10"
      rebalance_days = 10
      name = "MOM10 Top10 10D Paper Demo"
      capital = 100000
      start_date = "2026-09-18"
      status = "PAPER"
    } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/live/create -ContentType "application/json" -Body $body

Refresh the Live Portfolio and inspect rebalance_workflow. A signal can be
scheduled in advance; the Apply button becomes available only when its execution
session is due.

### Demo F: inspect a portfolio and export it

    Invoke-RestMethod http://127.0.0.1:8765/api/live/portfolios?view=active

    $body = @{ id = "<PORTFOLIO_ID>"; refresh = $true } | ConvertTo-Json
    $detail = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/live/detail -ContentType "application/json" -Body $body
    $detail.rebalance_workflow

    $body = @{ id = "<PORTFOLIO_ID>" } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/live/export -ContentType "application/json" -Body $body

### Demo G: research audit

    $body = @{
      universe = "ETF_250M"
      signal_date = "2026-09-04"
      lookback = 10
      mode = "Top Ranking Audit"
    } | ConvertTo-Json
    $audit = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/research-audit -ContentType "application/json" -Body $body
    $audit.comparisons
    $audit.export_path

Use an exact open signal date present in the local calendar. If the date is at
the end of the available data, the backtest comparison may correctly report
that the range is too short for a next-session execution.

### Demo H: offline calculation sanity check

The core calculation functions can be tested without the browser or network:

    python -m unittest discover -s tests -p "test_*.py"
    node tests/test_ui.cjs

The test suite includes synthetic data for integrity, Grid-to-Live promotion,
paper/actual ledger behavior, staged worker failures, historical reconstruction,
pagination and UI rendering.

## 15. HTTP route reference

All requests must be same-origin localhost requests.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | / | Browser UI |
| GET | /api/health | Runtime health and roots |
| GET | /api/config | Current settings |
| GET | /api/status | Dataset status |
| GET | /api/job-status | Data-job state, stage, errors and worker activity |
| GET | /api/calculation-status | Research calculation progress |
| GET | /api/universes | Universe status and active universe |
| GET | /api/live/portfolios | Portfolio list |
| POST | /api/config | Save settings |
| POST | /api/test-tushare | Test API connectivity |
| POST | /api/full-build | Start staged full build |
| POST | /api/refresh | Start incremental update |
| POST | /api/job-cancel | Request safe cancellation |
| POST | /api/job-resume | Resume a checkpointed job |
| POST | /api/job-reset | Reset stale job state |
| POST | /api/build-adjusted | Rebuild adjusted prices in staging |
| POST | /api/validate | Run local integrity validation |
| POST | /api/repair-safe | Explicit duplicate-key repair |
| POST | /api/run | Run Research Lab |
| POST | /api/strategy-grid | Run Strategy Grid |
| POST | /api/research-audit | Generate research audit |
| POST | /api/live/create | Create a verified-source portfolio |
| POST | /api/live/detail | Load/refresh portfolio detail |
| POST | /api/live/rebalance-preview | Preview model rebalance/capital flow |
| POST | /api/live/paper-rebalance | Apply PAPER target |
| POST | /api/live/actual-rebalance | Record ACTIVE fills |
| POST | /api/live/trade | Record or correct a manual trade |
| POST | /api/live/journal | Journal a completed actual rebalance |
| POST | /api/live/export | Export portfolio files |
| POST | /api/live/archive | Archive portfolio |
| POST | /api/live/restore | Restore portfolio |
| POST | /api/live/promote | Continue PAPER as ACTIVE |
| POST | /api/live/delete | Permanently delete one portfolio after confirmation |

## 16. Troubleshooting

| Symptom | Likely explanation | Action |
| --- | --- | --- |
| Browser does not load | Backend/resource path/startup failure | Check /api/health, logs/momentumlab.log and logs/launcher-error.log |
| Integrity FAIL | Structural or factor/classification problem | Read individual checks; repair source/staging, then validate again |
| Structural checks pass but observations are missing | Completeness is assessed separately | Inspect completeness status and export the full coverage evidence |
| Stage 3 stays at 0% with no worker | Worker died or stale job state | Read /api/job-status; inspect traceback; Resume or Reset Stale Job |
| No API requests on incremental update | No missing open sessions or stage was already checkpointed | Confirm SKIPPED / ALREADY CURRENT and requested end date |
| Apply Paper Rebalance disabled | Signal target is not generated or execution date is not due | Refresh portfolio; wait for the next open execution session |
| New Grid list does not replace Live Portfolio | Grid runs are immutable saved research; Add creates a new portfolio | Refresh the existing portfolio or create a separately named version |
| Audit says Research/Strategy Grid UNAVAILABLE | No exact saved target/evidence for that date and parameters | Use the exact saved signal date and retain the run folder |
| Backtest says range too short | No complete signal-to-next-session window | Extend the dataset/end date or choose an earlier signal date |
| Results folder is large | Charts, histories and audit packages are intentionally retained | Back up and archive selectively; do not delete runs needed for provenance |
| Live actual NAV looks stale | Raw quote coverage ends earlier than requested as-of date | Update raw prices and inspect quote date/source labels |

## 17. Backup, upgrade and deletion policy

Before updating the application:

1. Stop the launcher and confirm no data job is active.
2. Copy data, results, live, config, logs and backups to a dated backup.
3. Install the new executable/source into a new folder.
4. Copy the mutable folders into the new folder.
5. Start the new copy and run integrity validation before deleting the old copy.

Do not delete results casually. It is not required for the application to start,
but it contains saved histories, run IDs, charts and audit evidence used to
reproduce or reconcile research. The live database is independent of results,
but its source-run references may become unavailable if the run folder is
removed.

Do not share config/settings.json while it contains an API token. Use a redacted
settings file for support or source control.

## 18. Verification checklist

For a normal release or data update:

1. /api/health returns ok: true.
2. Data Manager status identifies the intended active universe.
3. Integrity returns PASS or a consciously accepted WARNING.
4. Research Lab completes and writes a run directory.
5. Strategy Grid completes the requested rows and Full Period where selected.
6. Grid history contains signal-to-entry dates and turnover.
7. Live Portfolio creates or refreshes without changing prior audit records.
8. PAPER rebalance preview matches target weights and expected cash.
9. ACTIVE records contain actual fills and fees.
10. Research and portfolio exports open from the results folder.
11. Python and Node test suites pass.
12. The dataset manifest fingerprint is recorded with the run.

The implementation is reproducible within the chosen dataset and model
conventions. When comparing runs, compare the dataset fingerprint, universe,
strategy parameters, date range, cost rate and saved run ID together.

## 19. Glossary

| Term | Meaning |
| --- | --- |
| Signal date | Date on which MOM is ranked and a target is selected |
| Execution date | Next open exchange session after the signal date |
| MOM(N) | N-observation momentum score normalized by scaled sample volatility |
| Top10 | Ten highest valid MOM ranks, equal weighted |
| Q5 | Strongest momentum quintile in the displayed quintile convention |
| One-way turnover | Half the absolute weight change across old and target portfolios |
| Gross NAV | NAV before modeled trading costs |
| Net NAV | NAV after modeled turnover costs |
| PAPER | Automatic model-simulated portfolio |
| ACTIVE | Manual ledger requiring confirmed trades |
| Dataset fingerprint | SHA-256 identity of validated file contents/schema |
| Saved target | Immutable target snapshot tied to a signal date and provenance |
| T+1 | The next open trading session after the signal date |

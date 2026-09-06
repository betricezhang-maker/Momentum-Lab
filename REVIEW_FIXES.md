# Strategy Grid execution trace and follow-up fixes

## Execution flow

1. `ui/index.html: runStrategyGrid()` reads `.gridSel` checkboxes into `sels`, along with universe, costs, MOM lookbacks, rebalances and years. It POSTs `selections: sels` to `/api/strategy-grid`.
2. `Handler.handle_post()` dispatches that endpoint to `run_strategy_grid(body)`.
3. `run_strategy_grid()` normalizes `selections` and computes signals/ranks once per lookback. Its nested `add_period()` iterates periods, lookbacks, rebalances and `selection_rule`.
4. `backtest(ranked, selection_rule, ...)` uses its local `selection` parameter in `select_mask()`. Its calculation body is unchanged.
5. Each result/error row records that exact `selection_rule`. CSV output contains all selections. The outer response uses the defined `selections` list for both `selection` and `selection_rules`, never a nested loop variable.
6. The UI keys rows by period, MOM, rebalance **and selection**. The primary table displays every portfolio. The sensitivity matrix has its own portfolio selector.

The reported `NameError: name 'selection' is not defined` was not reproducible in the starting source: its outer response already used `selections`. There was no matching traceback in the supplied log. Single/multiple portfolio runs passed before editing. An older running process or another copy remains a possible source, not a confirmed diagnosis. Restart the application from this source; `/api/health` should report build tag `strategy-grid-selection-labels-2`. A separately packaged executable requires rebuilding.

## Implemented fixes

- Explicit selection-rule scope; regression coverage for single, multiple, legacy and failed grid runs.
- Portfolio-aware grid tables and heatmap; errors/coverage displayed rather than silently hidden. Incomplete years cannot outrank complete coverage simply through a smaller denominator.
- Null metric handling and lower-volatility green shading.
- Detailed captions, actual dates, costs, universe, portfolio, MOM/rebalance settings, units, execution assumptions and sample-count tooltips. Diagnostics explicitly exclude rebalance/cost modeling. Mini trends identify adjusted-price units and observation-based horizons.
- Charts and result tables describe the existing CAGR/annualized-volatility ratio and first-post-entry NAV normalization accurately.
- JSON run metadata and the actual membership/eligibility data used are saved alongside results. Unique run directories avoid same-second collisions.
- Checkpoints mark buffered downloads only after CSV persistence. CSV and settings writes use atomic replacement.
- Updated nonempty membership snapshots replace old constituents instead of retaining removed names.
- Incremental download windows repair per-security trailing/internal gaps and missing adjustment-factor dates; newly encountered securities receive the available historical window. Suspension gaps may be re-requested conservatively.
- Top100 candidate discovery includes listed, delisted and paused securities.
- Missing factors stop adjusted-price rebuilding before output replacement. Existing adjusted data receives a provenance warning when factor evidence is incomplete.
- Research validation checks empty data, invalid dates/codes, duplicate keys and nonpositive/nonfinite adjusted prices.
- Resume can recover request details from disk. Completed-job polling does not reload all CSV status every second.
- Imports are separated by universe and remembered in the universe path map. Exact data-relative paths can relocate when a portable folder moves.
- HTTP mutations are serialized; settings/import/research operations cannot overlap an active data job. Result downloads are restricted to the results folder; Host/Origin checks restrict browser requests to same-origin localhost.
- Version and build identifiers are consistent.

## Deliberately unchanged / remaining limitations

- Signal, ranking, selection, forward-return and backtest mathematical functions are unchanged, verified by structural hashes. Initial-cost normalization, CAGR/volatility calculation, equal-weight fills and suspension valuation assumptions therefore remain as before and are now labeled.
- Existing CSI300 raw-file configuration points to a missing file. Locate/import the intended raw dataset explicitly; relocation does not guess that another differently named CSV is equivalent.
- Existing CSI300 factor history remains incomplete. No user datasets were downloaded, reconstructed or overwritten. A historical factor download and rebuild are required to repair that data.
- Old checkpoints may already describe previously lost data. These changes prevent new premature checkpoints; they cannot reconstruct data that was never persisted.
- ETF classification still requires Tushare at run time. Saving the membership used improves auditability but does not make ETF research offline.
- Strategy Grid still runs synchronously per HTTP request; there is no grid progress/cancellation worker. The application remains monolithic, with no CI or dependency lockfile. File locks do not coordinate separate application processes.
- Same-date membership availability is still assumed. Publication-time modeling and realistic execution would change the research methodology.

## Verification

Run `python -B -m unittest discover -s tests -v` with pandas/numpy installed, and `node tests/test_ui.cjs`.

Tests use synthetic data and temporary storage. They cover grid/backtest/CSV/JSON flow, selection identity, failed rows, durability, snapshot replacement, validation, incremental repair and unchanged mathematics. A separate synthetic full-research run with matplotlib also generated all PNGs, CSVs and a valid JSON response. No live Tushare calls or production-data backtests were used for verification.

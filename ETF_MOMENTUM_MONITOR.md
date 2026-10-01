# ETF Momentum Monitor

Restart the usual launcher, refresh the browser, and run ETF_250M in Research Lab. The existing five-panel block updates automatically. Existing results are not rewritten.

## Coverage diagnosis

Saved run ETF_250M_MOM10_20260928_212013_204cc897 contains 137 signal dates. Under the former 100% coverage rule, participation was unavailable on 96, whereas all 137 Top10 averages were valid. Median eligible MOM10 coverage was 99.52%, minimum 95.73%. The clarified rule now uses a configurable minimum, default 80%, and divides positive valid scores by valid scores. All 137 saved dates meet that default coverage threshold. Existing saved results and the historical investigation CSV retain their original settings.

The hash-matched saved-data investigation found 278 ETF-date exclusions with incomplete exchange-session windows or invalid prices, and 20 with no adjusted-price observation on the signal date. See diagnostics/etf_momentum_coverage_20260928.csv for every date, ticker, count and reason. These counts are ETF-date occurrences, not unique ETFs.

## Measures

1. Existing daily net cumulative return: (net equity - 1) * 100. No renormalization, backtest rerun or cost change.
2. Positive production MOM10 participation: eligible ETFs with positive valid MOM10 / eligible ETFs with valid MOM10. Valid-score count / eligible count must meet the configured minimum (default 80%, inclusive); zero valid scores remain unavailable. Set **ETF participation minimum coverage (%)** beside the Research Lab date controls before running. The selected threshold is saved in run metadata and each CSV observation. Coverage, excluded counts and ticker-level reasons remain visible even when a reading is available. Production completeness rules are unchanged; invalid scores never enter the denominator or become zero momentum.
3. Mean production MOM10 of actual selected Top10, dimensionless. Incomplete holdings, non-Top10 selections and execution/signal-target mismatches remain unavailable.
4. Median Eligible ETF 60-Session Return (%): median of adjusted close at signal / adjusted close 60 exchange sessions earlier - 1. Each included ETF requires all 61 consecutive, positive finite closes. At least 80% eligible coverage and one valid return are required. This diagnostic threshold does not override data-integrity or MOM requirements. The median is not an investable benchmark or equal-weight portfolio return.

## Historical comparison

All five panels share identical date limits. Strategy observations remain daily; indicators retain signal-date observations with connected lines and visible markers. Lines break at unavailable values; no values are filled. Charts use existing static Matplotlib infrastructure without hover.

Two display toggles switch saved chart images without rerunning research: **Five-rebalance trailing median** uses the current and four preceding consecutive valid readings, restarting after a missing observation; **Color markers by subsequent realized net return** shows completed positive outcomes green, negative red, and zero/unavailable outcomes grey. Outcome colors are retrospective diagnostics, not information available at signal time. Three separate scatterplot panels pair each indicator with its saved subsequent net holding-period return, excluding unavailable indicators or incomplete/unavailable outcomes. Each reports its sample size. Exact values remain in the table and CSV. The original indicator values, cumulative return, ranking and execution logic are unchanged.

The paginated coverage table and etf_momentum_observations.csv show eligible, valid, positive and excluded counts, coverage, ticker-level exclusion reasons, indicators, signal/execution dates, subsequent holding return and holding-end date. Subsequent net return is next execution NAV before costs / current execution NAV before costs - 1. It includes the current cost once and excludes the next execution cost. Saved production NAV is reused. Missing held-security prices make the associated diagnostic return unavailable because saved NAV may use a fallback. The final incomplete period is also unavailable.

For the inspected run, 130/137 median readings and 131 subsequent net returns are available. Five completed periods lack held-position price coverage; the final period is incomplete.

## Point-in-time limitations

The latest production eligibility snapshot on/before each signal is reused with production equity classification and adjusted-price conventions. No future snapshot is backfilled. Historical classification publication vintages and intra-snapshot membership changes are not available, so strict point-in-time classification cannot be independently verified. This limitation is visible in the panel. No missing prices are fabricated, confirmations changed, or integrity gates relaxed.

## Verification

Six focused Python tests and the focused UI test pass, including formulas, signal alignment, future-data invariance, missing coverage, shared chart limits, exact net-curve equality and automatic ETF-only integration. Full Python suite: 143 tests, 138 passed, 3 skipped, two pre-existing failures (benchmark missing-date expectation and Top20 name-field expectation). Existing JavaScript integrity mock limitations are separate from this patch. Production-browser execution was not verified for this amendment. Production strategy and live-portfolio functions were not changed.

## Fifth panel: subsequent holding-period returns

Bars reuse the existing saved subsequent_net_return and completion status. Each completed return is drawn at its originating signal date: green positive, red negative, grey zero. Incomplete or unavailable periods have no bar, and their omitted count is shown. The existing execution-to-execution cost convention is unchanged: current execution cost included, next execution cost excluded. The first four series and the daily cumulative net-return calculation are unchanged. All five panels share a date axis; display toggles do not change bar values.

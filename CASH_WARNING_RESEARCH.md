# Mom101010 cash-warning feasibility study

## Finding

The available retrospective walk-forward evidence does **not** demonstrate a reliable cash-warning economic benefit. None of seven predefined conditions beats uninterrupted Mom101010 in aggregate over the same evaluation dates. Some warnings helped in 2022 but missed later gains; this is not a reason to change production rules.

Source: saved run `ETF_250M_MOM10_20260928_214542_e342a017`, ETF Top10 / MOM10 / 10D, production cost 5 bps. Original saved outputs, indicators, cumulative curve, Strategy Grid and live records are untouched. Research artifacts are in `diagnostics/cash_warning_20260929`. Future matching Research Lab runs add a **Cash-Warning Research** section automatically. Restart the launcher and refresh the browser to load the code.

## Predefined design

Individual warnings: low positive MOM10 participation; weak average selected Top10 MOM10; negative median eligible ETF 60-session return. Then all three pairwise AND combinations and the three-way AND. No threshold sweep, optimized lookback/hold/TopN, fitted model or automatic cash allocation.

For each calendar year, use completed earlier holding periods ending strictly before that year's first signal. Require at least twenty valid prior participation and Top10 observations. Freeze their lower-third quantiles throughout the evaluation year; use strict less-than, so threshold ties do not warn. Median60 uses the natural zero boundary; zero does not warn. 2021 supplies initial history; 2022–2026 are unseen-year evaluations, with 2026 partial. Training and evaluation tables are separate. Training statistics reuse their calibration sample and are optimistic; do not pool them as independent evidence.

Warnings use signal-time indicators only. Outcome availability never determines whether to flag. Missing required indicators mean no decision and continuation of baseline exposure. Incomplete final holding period is omitted. Saved signal/execution joins and net holding-return values are checked against production rebalance NAV records; overlapping or noncontiguous execution intervals are rejected.

## Economic assumptions

Cash earns 0%. Each cash/stock state transition deducts 0.5 times the configured cost rate: 2.5 bps at the saved 5-bps setting, following the existing securities-only one-way turnover convention. Invested intervals keep their original modeled production costs and also pay the extra cash re-entry charge. Cash intervals avoid the baseline holding return and baseline rebalance cost. This is a conservative additional-cost proxy, not a reconstructed alternate order book. No terminal liquidation charge is imposed; annual comparisons restart invested, while the aggregate carries state between years.

Daily hypothetical curves splice saved net NAV segments, with next execution NAV taken before its next cost. Current interval cost is included once, next interval cost belongs to the next period. The no-warning curve equals the matching uninterrupted baseline exactly. Comparison curves normalize the evaluation start to 1; the existing full Research Lab cumulative curve is not modified.

Three of 113 evaluation periods have missing held-price evidence. Their saved baseline NAV may contain valuation fallback. They remain in the provisional economic curves so the time path is not silently shortened; correctness/false-alarm metrics exclude their unknown outcomes. Flags still depend only on signal-time inputs. Strong economic inference and confidence intervals are withheld. This limitation is visible in the UI and chart.

When every evaluated outcome is supported and enough observations exist, mean incremental period-benefit uncertainty uses a five-observation chronological moving-block bootstrap, 500 replicates with deterministic seed. This is not an independent-observation confidence calculation and has no multiple-testing adjustment. Retrospective OOS tests are not prospective validation.

## Results over matching unseen-year coverage

113 periods, three unassessable outcomes; no overlapping execution windows. Baseline cumulative net return **226.07%**, maximum daily drawdown **−22.60%**.

| Warning | Flagged | Losses identified | False alarms | Cash-proxy cumulative return | Cash-proxy max drawdown |
|---|---:|---:|---:|---:|---:|
| Low participation | 40 | 32% | 57.89% | 96.14% | −26.68% |
| Weak Top10 MOM10 | 34 | 28% | 56.25% | 137.88% | −21.09% |
| Negative median60 | 60 | 52% | 54.39% | 52.74% | −20.11% |
| Low participation AND weak MOM10 | 28 | 24% | 53.85% | 149.62% | −26.86% |
| Low participation AND negative median60 | 32 | 26% | 56.67% | 100.67% | −23.74% |
| Weak MOM10 AND negative median60 | 24 | 20% | 54.55% | 160.52% | −20.10% |
| All three | 22 | 18% | 55.00% | 153.67% | −25.11% |

Loss recall is flagged assessable losses / all assessable losses. False-alarm frequency is flagged non-losses / assessable flagged periods; exact zero is non-loss. Positive returns missed and negative returns avoided are also exported as arithmetic sums of period returns, explicitly distinct from compounded economic returns.

2022: 24 evaluation periods; baseline −14.93%. Low participation cash proxy was −1.64%, weak MOM10 −10.67%, negative median60 −6.96%. Although all seven proxies reduced that year's loss, none improved aggregate cumulative return. This illustrates why selecting solely on the known 2022 decline would be misleading. Several annual flagged samples are small. All annual training/OOS rows, thresholds, warnings, true/false classifications, missed gains, avoided losses and costs are exported.

## Files and verification

- `cash_warning_report.json`: assumptions, conclusion, provenance, all tables.
- `cash_warning_folds.csv`: chronological calibration boundaries and thresholds.
- `cash_warning_decisions.csv`: exact signal-date warnings and realized outcomes.
- `cash_warning_metrics.csv`: training versus unseen-year annual results.
- `cash_warning_aggregate.csv`: aggregate comparative returns/drawdowns and warning metrics.
- `cash_warning_curves.csv`, `cash_warning_equity.png`: matching daily comparison curves.

Focused tests cover prior-only calibration, unchanged inputs, exact no-warning baseline, transition costs, missing inputs/outcomes, exclusion of final incomplete periods, overlap rejection and disagreement with saved NAV. The full regression run had 155 tests: 150 passed, three skipped, and two pre-existing failures (benchmark missing-date expectations and Top20 name-field expectations). An additional NAV-consistency test was added afterward and run in the focused suite. UI rendering tests are separate; the production browser was not restarted or verified.

Historical membership uses production snapshots; classification publication vintages, vendor revisions, intraday liquidity, actual cash deployment and alternate order costs remain unverified. Further work could improve data evidence and prospectively monitor the predefined diagnostics, but these results do not justify a cash-warning trading rule.

# CSI 300 adaptive rebalance feasibility study

Exploratory, research-only study: can the CSI 300's observable trajectory act as a leading indicator for choosing between 5-session and 10-session rebalance intervals of the existing ETF_250M / MOM10 / Top10 strategy? The hypothesis was developed after inspecting historical results, so every finding below is in-sample and retrospective. No production strategy, file or default was changed; Mom101010 remains the unchanged champion regardless of outcome.

## Predefined rule (fixed before evaluation, not optimized)

At each rebalance signal date, compute the trailing 20-session total return of the CSI 300 total return index (H00300.CSI, the Strategy Grid total-return convention). Reading below zero selects the 5-session interval for the next holding period; zero or above selects 10 sessions. Fewer than 21 benchmark observations at the decision date defaults to 10 sessions and is counted. The 20-session window and the zero threshold were not tuned.

## Method

One stateful simulation walks the production calendar with a variable step: the interval chosen at signal date t sets the next signal at t + interval, with holdings, cash and NAV persisting across switches - not a splice of saved 5D and 10D curves. The simulation loop reproduces production `backtest()` exactly (T+1 execution, equal weight, price-gap skips, weight-based one-way turnover); the fixed 5D and 10D arms run through the identical loop and are asserted equal to production `backtest()` output before any comparison. Costs follow the V4 convention: gross simulation cost-free, net = gross x product(1 - one-way turnover x 5 bps) per entry, initial funding included. The leading-indicator diagnostic is kept separate from the continuous simulation: a paired-basket event study on the fixed 10-session grid compares, at each mid-point pseudo signal date t+5, the forward 5-session return of the fresh Top10(t+5) basket against the continuing Top10(t) basket over the identical window [t+6, t+11], conditioned on the trailing reading at t+5.

Window 2021-02-01 .. 2026-10-09 (start is the first eligible ranking date), dataset ETF_250M fingerprint c8ede0e6..., benchmark H00300.CSI 2021-01-04 .. 2026-10-09.

## Reconciliation

- Study simulator == production `backtest()` for both fixed schedules: equity, logs and stats match exactly.
- Fixed 10D net stats == champion archive `ETF_250M_MOM10_20261010_063502_57b6d7e8` exactly (same dataset fingerprint).
- The 2026-09-18 fixed-5D archive predates a dataset update (fingerprint 2d2726... vs current c8ede0...); re-running production `backtest()` on the current dataset through 2026-09-18 reproduces the study baseline exactly, so the archived numeric differences are dataset drift, not a pipeline difference. All three study arms use the current dataset consistently.

## Full-period results (net of 5 bps one-way turnover costs)

| Strategy | Rebalances | Cumulative net return | Net CAGR | Net max drawdown | Total one-way turnover |
|---|---:|---:|---:|---:|---:|
| Fixed 10D (champion Mom101010) | 138 | 228.97% | 24.41% | -29.47% | 128.81x |
| Fixed 5D | 275 | 92.22% | 12.73% | -37.50% | 222.99x |
| CSI 300 adaptive 5D/10D | 203 | 117.27% | 15.29% | -32.26% | 172.48x |

Annual net returns (2021 partial from 02-01, 2026 partial to 10-09): the adaptive arm trails the champion in every year except 2024 (35.00% vs 31.52%) and 2025 drawdown (-12.09% vs -13.41%); its worst relative years are 2023 (3.27% vs 22.43%) and 2026 to date (2.36% vs 17.83%).

## Leading-indicator diagnostic

| Trailing reading group | n | Mean 5D-10D forward advantage | Hit rate 5D>10D | t-stat |
|---|---:|---:|---:|---:|
| Negative (5D would be chosen) | 75 | -0.49% | 44.00% | -1.72 |
| Non-negative (10D retained) | 62 | -0.20% | 43.55% | -0.56 |
| All usable events | 137 | -0.36% | 43.80% | -1.60 |

After negative CSI 300 readings, the fresh 5D basket actually underperformed the continuing 10D basket by 0.49% per five sessions on average - the opposite of the hypothesis. 5D's disadvantage is not concentrated in any benchmark regime; the benchmark sign carries no leading information about relative 5D/10D performance. Advantages are gross of costs; the cost consequence of switching is captured inside the portfolio simulation.

## Adaptive schedule behavior

203 decisions: 131 chose 5D, 72 chose 10D, 1 defaulted to 10D (2021-01-29, only 20 benchmark observations available). 58 interval switches; the schedule is twitchy, with median holding spells far shorter than either fixed arm. Per-decision readings are in `decisions.csv`.

## Conclusion

The adaptive rule does not improve either full-period net return or maximum drawdown (117.27% / -32.26% vs 228.97% / -29.47% for the unchanged champion), and the diagnostic shows the benchmark sign has no leading association with 5D-vs-10D relative performance - if anything a mildly inverse one. The negative result is preserved: Mom101010 is left unchanged, and no production component was modified. In-sample improvement would not have established out-of-sample value given the rule's post-hoc origin; no such improvement exists to claim.

## Verification

Study script `studies/adaptive_rebalance_study.py` (benchmark arg `csi300`, the default); artifacts in `results/studies/csi300_adaptive_rebalance/20261010_073100` (`report.md` with the full decision and switch tables, `net_equity.csv`, `decisions.csv`, `diagnostics_events.csv`, `annual_table.csv`, `equity_comparison.png`, `report_data.json`). Reconciliation asserts pass exactly for both fixed arms against production `backtest()` and for the champion archive; the one flagged FAIL pair is the documented pre-update dataset drift of the 2026-09-18 archive. One event retained with partial pricing (9 of 10 names) is flagged; missing benchmark readings are reported, never imputed, and no comparison was shortened to raise coverage.

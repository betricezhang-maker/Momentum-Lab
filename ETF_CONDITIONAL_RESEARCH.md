# Stage 1 conditional ETF research

Run ETF_250M / MOM10 / Top10 / 10D in Research Lab after restarting the launcher and refreshing the browser. A conditional analysis section is appended to the existing diagnostic output. Other strategies do not receive this initial-case analysis. Existing indicators, plots, backtests and portfolio state are unchanged.

The split is the expanding median of at least 20 preceding valid participation observations, excluding the current observation. Participation at or below the split is Low; above is High. Zero participation is valid. Exactly zero median60 is a separate excluded neutral observation; missing data and incomplete outcomes are counted explicitly. Outcome values are existing completed execution-to-execution net holding returns, associated with originating signal dates.

The original heatmap has four cells, each with sample count and mean return. Supporting tables contain median, profitable frequency (strictly positive), minimum, quartiles, maximum and mean confidence interval. Raw joined observations preserve the complete empirical distribution, thresholds and exclusion reasons. Annual rows group by signal year, including 2022. Fewer than ten observations is flagged insufficient; groups with fewer than twenty receive no confidence interval.

Uncertainty uses 500 deterministic moving-block bootstrap samples of chronological rebalance observations, block length five. Group membership and outcomes are resampled together, including missing/excluded positions in the time grid. Returns are not resampled as independent individual observations. Overlapping eligible return windows suppress all confidence intervals; descriptive counts still appear. These exploratory intervals are sensitive to block length and structural changes, and have no multiple-testing adjustment.

The optional sensitivity view removes exactly one globally largest positive eligible outcome; a tie removes the earliest signal. The same removal applies to every annual/subgroup view. Original results and past-only thresholds are preserved. This is sensitivity analysis, not a justified trading exclusion.

Top10 MOM10 is compared within each of the four groups by splitting at the median of that group's preceding valid Top10 scores (minimum ten). Ties are Low. Unclassifiable subgroup history is counted. Differences in mean returns are descriptive only, without a fitted model or claim of added predictive power.

## Saved-case findings

Source: `ETF_250M_MOM10_20260928_214542_e342a017`. Separate deliverables are in `diagnostics/etf_conditional_stage1_20260929`; the source run was not rewritten.

| Participation / median60 | n | Mean net holding return | Median | Profitable |
|---|---:|---:|---:|---:|
| Low / Negative | 36 | 0.96% | 0.36% | 52.78% |
| Low / Positive | 24 | 0.12% | -0.79% | 45.83% |
| High / Negative | 21 | 1.69% | 0.38% | 57.14% |
| High / Positive | 31 | 1.37% | 1.09% | 58.06% |

112 of 137 signals qualify. The excluded 25 reflect unavailable indicators, initial threshold history and incomplete/missing-price outcomes. No eligible windows overlap. Removing the +25.80% outcome originating 2024-09-18 reduces Low/Negative mean to 0.25% (n=35). The other three means are unchanged.

2022 counts are 11, 4, 6 and 2 respectively; means are -1.27%, -0.89%, +0.38% and -1.61%. These small annual groups do not support confident conclusions. Top10 MOM10 subgroup differences have no consistent direction and several subgroups contain fewer than ten observations.

Historical classification vintages and intra-snapshot membership changes cannot be reconstructed. Existing adjusted-price, coverage and missing-held-price exclusions remain in force. No trading rules, automatic allocations or production confirmations are changed.

## Verification

Four focused statistical-data tests pass: past-only thresholds and source immutability; zero/missing/incomplete exclusions and one-outlier removal; deterministic block uncertainty and overlap suppression; direct return-distribution agreement. UI tests cover the annual table, small-sample warnings and optional sensitivity view. Full Python regressions: 151 tests, 146 passed, 3 skipped, two existing failures concerning missing benchmark-date expectations and the Top20 name field. The heatmap was rendered and visually inspected. The running production browser was not restarted or tested for this amendment.

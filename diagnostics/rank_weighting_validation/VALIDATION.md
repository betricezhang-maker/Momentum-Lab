# Rank-weighting research validation

Source run: `ETF_250M_MOM10_20260930_183005_4c34c035`.
No original source results, dataset files or live portfolio records were edited.

The equal-weight gross-share replay and shared cost overlay reproduce the full
production net curve with maximum absolute NAV error 7.99e-15. Actual production
turnover also reconciles. The separate completed-period baseline reconciles to
the original NAV with the final execution's next-period cost excluded.

Completed comparison: 2021-02-01 through 2026-09-11; 136 holding periods.

| Variant | Net cumulative return | CAGR | Maximum drawdown | Average recurring turnover |
|---|---:|---:|---:|---:|
| Equal weight | 263.77% | 27.03% | -29.47% | 93.93% |
| Mild gradient, alpha 0.5 | 248.30% | 26.01% | -30.85% | 94.58% |
| Linear gradient, alpha 1 | 234.33% | 25.06% | -32.15% | 95.18% |

Average within-period rank-strength Spearman: -0.060. On complete Top10 periods,
ranks 1–3 minus ranks 8–10 averaged -0.278 percentage points. Rank means were not
monotonically ordered. Neither gradient improved cumulative net return. These
are descriptive findings, not validated forecasts or a production recommendation.
Eight ticker-period observations were excluded; portfolio curves preserve the
production missing-price valuation convention and therefore remain provisional.

Verification:

- Focused weighting plus inherited ETF-monitor checks: 14 passed.
- Equal-weight mismatch and reversed rank order stop interpretation.
- Tests verify normalized weights, drift-aware turnover, initial and recurring
  costs, annual compounding, input immutability, missing-observation exclusions,
  future-price isolation and reproducible output files.
- JavaScript checks pass for Off default, control placement after coverage,
  explanatory failures, research-only rendering and clearing old results.
- HTML structure check places controls and output only inside Research Lab;
  page JavaScript parses. Equity chart visually inspected.
- Full regression attempt: 173 tests; one failure, six errors, three skips.
  Five errors were missing `requests` in the isolated test environment. After
  installing that existing dependency there, the affected modules plus weighting
  tests ran 30 tests successfully (three skipped).
- Two existing unrelated mismatches remain: the benchmark test expects a missing
  benchmark date to be dropped, while production rejects it; the performance test
  expects Top20 output without the previously added ETF name field.
- Live desktop browser interaction was not verified. No running server was
  restarted as part of this patch.

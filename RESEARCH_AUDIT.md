# Research calculation audit

Open **Audit / Export → B. Research / MOM Calculation Audit**. Choose the universe,
exact open signal date, MOM lookback and mode. Single Ticker mode requires a ticker.
Top Ranking shows ranks 1–20; Full Signal shows all valid rankings. Every mode creates
a complete portable ZIP, independently of the portfolio event store.

The adapter reads the retained output of `calculate_signal()` and
`merge_membership_and_rank()` through the existing market provider. A shared calendar
gate excludes incomplete windows. The unchanged arithmetic uses N returns from N+1 prices,
`pct_change(fill_method=None)`, sample SD (`ddof=1`, `min_periods=N`) and sqrt(N).
These prices must cover exactly the last N exchange sessions and their starting price.
Missing sessions are reported and invalidate the score; they are never filled or bridged.
Equal scores retain production
`rank(method='first')` order. Near ties are only a diagnostic.

The current snapshot is the latest saved membership snapshot on or before the signal
date. Historical ETF size comes from that snapshot. The separate classification file
has no historical timestamp; its category check is labeled as undated evidence.
The audit cannot reconstruct securities absent from all local historical inputs.

Target comparisons use the production selector, preview target snapshot and actual
production backtest. Saved Research Lab and Strategy Grid histories are compared only
when an exact MOM / Top10 / 10D signal row exists. A target match and a dataset fingerprint
match are separate facts. Missing saved rows show UNAVAILABLE, never an inferred MATCH.
Execution uses the exchange calendar; a difference from the backtest's observed-price
calendar is exposed as a mismatch, without changing execution logic.

Each run persists under `results/research_audits/<audit-id>/` with a ZIP beside it:

- audit_manifest.json (identity, formula conventions, integrity, comparisons)
- universe_snapshot.csv
- price_inputs.csv
- daily_returns.csv
- mom_calculation.csv
- ranking.csv
- top10_target.csv
- excluded_securities.csv

CSV floating-point values use 17 significant digits. Returns and weights are fractions.
In Excel use last price / first price − 1, STDEV.S(last N returns), SQRT(N), then divide
the return by scaled volatility. Rank unrounded scores using the documented tie rule.
The files remain tied to the audit's fingerprint even after later data updates.

Verified locally on 2026-09-15: ETF_250M, 2026-09-04, MOM10, 159208.SZ:
11 prices, 10 returns, return 0.05859375, sample SD 0.007745324652031118,
scaled volatility 0.02449286711786943, MOM 2.3922781158295408, rank 1,
execution 2026-09-07. Dataset fingerprint starts `f7af8a71b51e4238` and integrity
was WARNING. Backtest and preview snapshot matched; exact saved research/grid
targets were unavailable. Revalidate when using a different dataset fingerprint.

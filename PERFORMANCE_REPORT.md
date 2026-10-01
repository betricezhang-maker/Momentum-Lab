# V4 performance patch report

The benchmark uses the local `ETF_250M` dataset, two MOM spans (10/20), two
selections (Top 5/Top 10), one 10D rebalance period and 2026 data. RAM is sampled
from the Windows working set every 50 ms. The five-repeat artifacts are under
`results/performance/verified/`; the baseline is under `results/performance/before/`.

| Workflow | Before | After (five runs) | Peak before | Peak after |
| --- | ---: | ---: | ---: | ---: |
| App import | 2.6 s | 1.1 s | idle 109 MB | idle 82 MB |
| Data Manager | 12.1 s | 3.1 s | 563 MB | 513 MB |
| Integrity, cold | 67.6 s | 36.2 s | 863 MB | 858 MB |
| Integrity, warm | 0.010 s | 0.010 s | 111 MB | 100 MB |
| Strategy Grid | 42.4 s | 32.0–36.5 s | 1,499 MB | 973–1,044 MB |
| Full Research | 87.8 s | 39.3–42.5 s | 1,914 MB | 1,049–1,170 MB |
| Live detail | 57.8 s | 42.2 s | 1,020 MB | 1,067 MB |
| Audit | 12.3 s | 11.1 s | 1,621 MB | 704 MB |

The live-detail peak is measured with the bounded market cache populated; the
cache is capped at one entry / 384 MB and expires after two minutes. Python may
retain freed arenas in the process working set, but no stale DataFrame remains
reachable from the runtime cache after expiry or Clear Runtime Cache.

Measured baseline causes were duplicate full adjusted-price reads, repeated date
normalization during validation, five ranking calculations for two requested
research spans, and two full ranking tables retained by Grid. The patch reuses a
request-local primary/secondary ranking, processes Grid lookback combinations
one at a time, reads only the columns needed by Data Manager status, streams file
hashes, and bounds small metadata and live-market caches. Large Audit and live
tables render 50 rows per page; exports and underlying data are unchanged.

Validation:

- `tests/compare_performance_outputs.py verified` passed: fingerprints equal,
  numerical outputs within 1e-12, histories/NAV CSVs equal, and core MOM/rank/
  backtest function ASTs unchanged.
- Python suites passed: regressions, live, data integrity, historical preview,
  portable calendar, serialization, research audit and performance (5 new
  performance checks included). HTTP integration verification is recorded
  separately in the packaging validation report.
- Node suites passed: UI, preview UI, research audit UI and pagination.

Use **Settings → Clear Runtime Cache** after a large run if an operator wants to
release cached DataFrames immediately. This retains market data, saved results,
audit packages and portfolio records.

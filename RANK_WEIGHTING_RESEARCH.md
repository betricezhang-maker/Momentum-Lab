# Optional Top10 rank and weighting research

In Research Lab, enable **Top10 rank & weighting research** after the participation
and coverage controls, then run full research. It defaults to Off. This contained
study accepts ETF ≥ RMB250M, MOM10, Top10, 10-session rebalance and 5 bps costs.
Other parameter combinations show an explanation; their ordinary research runs
are preserved. Participation/cash settings do not affect this independent study.

The fixed variants are equal weights, normalized square-root rank weights
`sqrt(11-rank)`, and normalized linear weights `11-rank`. No optimization occurs.
Reverse mild and reverse linear variants are also included: normalized
`sqrt(rank)` and `rank`. These reverse the allocation across the same ten ETFs;
they do not select the bottom ten of the universe. Each reverse pair has the
same concentration as its forward counterpart. Reverse curves use dashed lines.
Production rankings, execution records, adjusted-price conventions and the shared
turnover/cost functions are reused. A separate gross-share replay is necessary
because production allocation is equal-weight only. Both its full equal-weight
NAV and turnover must reconcile to production within numerical tolerance before
interpretation. The completed-period baseline is checked separately.

Curves end immediately before the final saved execution's allocation/cost, so the
unfinished final holding period is excluded. Security forward returns require
complete observed exchange sessions; missing observations are excluded and counted.
Portfolio replay retains production forward valuation, with an explicit provisional
warning when data gaps exist. This does not repair or fill source data.

The panel includes rank mean/median returns, all three net curves, annual returns,
turnover, rank statistics, annual stability, leader retention and downloadable
records. Rank correlations are within-period Spearman correlations against rank
strength (11 minus rank). They are descriptive, without significance claims.
Initial funding is separately marked and excluded from recurring turnover averages.
Total modeled cost is expressed per initial NAV. Boundary years are marked partial.

HHI/effective holdings quantify concentration; complete-period gross sizing effects
and gross-versus-net cost drag describe attribution. They cannot causally separate
concentration from rank skill. No historical winner is a production recommendation.

Artifacts live under each **new** research run's `rank_weighting_research` folder.
The methodology JSON retains dataset provenance and assumptions. Existing results,
Strategy Grid, live portfolios, participation comparisons and production curves are
not modified. Restart the application and refresh the browser to load this feature.

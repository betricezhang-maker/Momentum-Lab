# Momentum Lab V4

V4 adds turnover and estimated costs to research, plus a durable manual/paper portfolio ledger. Momentum scores, ranking, selection and the zero-cost backtest remain the V3 baseline.

## Run from source

Use Python 3.10 or later:

```text
python -m pip install -r requirements.txt
python MomentumLabV2.py
```

The application prints its localhost address. Configure local datasets in Settings, validate them, then use Research Lab or Strategy Grid. Existing portable executables must be rebuilt to include V4.

Portable dataset resolution is shared by Data Manager, research, Strategy Grid and Live Portfolio. Paths inside the application folder are saved relative to the runtime root. Legacy settings pointing into an older portable `data`/`results` folder are resolved against the current copy, even if the original copy still exists. Source mode uses the script folder; packaged mode uses the executable folder. Bundled UI resources may use `_MEIPASS`; persistent data never does.

Live Portfolio's **Trading Calendar / Price Coverage** panel reports the portable calendar path, loaded date range, last open session, signal anchor, next dates and precise failure reason. Future exchange sessions remain available for scheduling even when prices end earlier. A present calendar that ends before the next anchored cycle is insufficient coverage; this patch does not extend or download it. Historical backtest sessions continue to come from ranked price dates, preserving the existing calculation convention.

## Research conventions

- Estimated cost presets: 0, 5 (default), 10 and 20 bps, plus custom.
- Securities-only one-way turnover is half the sum of absolute changes from drifted pre-rebalance weights to target weights. A completely replaced invested portfolio has 100% turnover; initial funding has 50%.
- Estimated cost equals one-way turnover times the rate. Initial cost is included in net NAV, but initial funding is excluded from recurring turnover averages. Gross statistics retain the V3 convention.
- Gross/net return, CAGR, drawdown, cost drag, retention, average/max/annualized turnover and per-rebalance holdings are available. Cost drag is a return difference, not the sum of cash fees.
- Grid rows retain the existing gross metric fields; V4 adds fields and saved rebalance histories. Quality score remains a display metric.
- ETF research requires positive equity classification and dated capitalization of at least RMB 250 million; missing required eligibility data fails validation.

## Live Portfolio

Run Strategy Grid, then use **Add to Live Portfolio** on a successful row. Confirm the name, start date, capital and ACTIVE or PAPER status. Multiple portfolios are stored independently in `live/momentumlab.sqlite3`.

Use **Refresh & Save Snapshot** to generate due model targets and persist NAV history. Historical targets generated later are labeled reconstructed. Signals use rankings available on/before the signal date; model execution uses the next available exchange date. If the calendar has not reached that date, execution waits for calendar coverage. Missing execution quotes are excluded and remaining weights are normalized; if all are missing the model keeps its prior holdings.

The **NEXT REBALANCE** panel processes saved signals in chronological order and displays signal and T+1 execution dates separately. Frequencies count exchange sessions from the saved signal anchor. Status moves through UPCOMING, SIGNAL READY, WAITING FOR EXECUTION, PARTIALLY EXECUTED, and COMPLETED or SKIPPED. Overdue signals retain their scheduled target instead of shifting to the date the app reopens.

The proposal compares current holdings with the new target and labels each row **NEW BUY**, **CARRY / INCREASE**, **CARRY / REDUCE**, **CARRY / NO MATERIAL CHANGE**, or **SELL OUT**, with NEW/CARRIED/EXIT status. It shows current and target RMB values, suggested value and quantity changes, and a final target-only portfolio. Optional contributions and withdrawals change investable NAV before allocation and are stored in a separate dated cash-flow ledger. Time-weighted returns and investment P/L exclude those external flows.

For ACTIVE portfolios, **Review / Record Actual Rebalance** prefills only the required BUY and SELL differences. Edit the actual date, price, quantity and fee; uncheck skipped fills; and choose whether the submitted batch completes the cycle. Partial batches preserve the model target and tracking difference. Each batch atomically appends trades, any capital adjustment, and a permanent before/target/after snapshot. For PAPER portfolios, **Apply Paper Rebalance** uses current compounded Paper NAV, the saved target, adjusted execution price and estimated model cost, then records the simulated quantities and snapshot. Initial funding remains separate from recurring turnover averages.

PAPER portfolios automatically follow model targets and calculate simulated holdings, NAV, turnover, costs, returns, rebalance history and position history. They do not accept manual trades and remain permanently labeled PAPER. ACTIVE holdings change only through manually recorded trades. Enter execution price, quantity and actual fees. Corrections append a new version; original entries and events remain available. Cash borrowing and overselling are rejected. For dividends, Price/Dividend is the total cash distribution; for splits, Qty/Split Ratio is the share multiplier. Record corporate actions explicitly.

Actual NAV uses raw closes and recorded cash flows; model and PAPER NAV use adjusted prices and estimated costs. Each position identifies the quote date and valuation source. Opening transfers preserve Paper quantities and basis when the raw and Paper unit prices reconcile. If their price units differ, activation stops with a reconciliation error rather than introducing a NAV jump or changing quantities silently. PAUSED suppresses new targets; CLOSED stops tracking while retaining holdings and history. Closed and archived portfolios have separate dropdown views, and archived portfolios can be restored.

For a retroactive run, choose whether the selected historical date is a **Signal Date** or an **Execution / Rebalance Date**, then choose an end date. An execution date maps to its preceding open signal session; the preview displays both dates and the ranked target before creation. Momentum Lab first reuses a matching saved Strategy Grid history when one exists. Otherwise it asks the existing backtest engine to generate the canonical target record for that signal anchor; the portfolio module never reranks it. The monitor, backtest and live adapter share the same target selector. Position History and Historical Model / Paper Rebalances show dated holdings, weights, quantities, prices, costs and NAV. Target diagnostics compare research, saved Grid/backtest and stored portfolio tickers, ranks and weights. Ending-NAV reconciliation and target mismatches remain visible.

Continuing a PAPER portfolio as ACTIVE transfers its current NAV, cash, quantities and cost basis into a linked actual ledger. It also carries the schedule anchor, last signal/rebalance, turnover history, saved snapshots, NAV history and cash-flow attribution. The Paper period remains labeled PAPER; subsequent confirmed fills are ACTUAL. Close freezes future tracking while preserving history. Archive hides a portfolio from the default view and supports restore. Permanent deletion requires typing `DELETE` and removes only records scoped to that portfolio ID.

Record a rebalance journal against the relevant saved signal after entering fills. It includes unjournaled buy/sell records within that signal-to-journal date range. Prior journals remain historical snapshots after trade corrections; the versioned ledger and audit provide the correction trail.

Audit / Export produces portfolio summary, positions, trades, rebalance history and NAV CSV files, plus a JSON audit. Back up the SQLite database while the application is stopped and retain source datasets and research results. Runtime ledgers and generated results are excluded from Git.

No broker connection or automated order submission is included. Updating local market data and refreshing portfolios are manual actions. Short records and stale quotes limit the interpretation of annualized metrics.

## Verification

```text
python -m unittest discover -s tests -p "test_*.py"
node tests/test_ui.cjs
```

The regression suite compares zero-cost calculations with committed V3 source, checks turnover and eligibility, validates ledger accounting and persistence, and exercises the real localhost API with isolated synthetic datasets. UI tests check rendering logic and JavaScript syntax; they do not replace a visual browser review.

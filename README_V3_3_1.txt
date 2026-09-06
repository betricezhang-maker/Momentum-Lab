Momentum Lab V3.3.3 — Strategy Grid / Robustness Test

WHAT'S NEW
- New Strategy Grid in Research Lab.
- Compare calendar year × MOM lookback × rebalance period automatically.
- Default MOM grid: 10 / 20 / 40 / 60 / 120 trading observations.
- Default rebalance grid: 5 / 10 / 20 / 40 / 60 trading days.
- Calendar years can be left blank to use all years in the local dataset, or entered as comma-separated years.
- Optional Full Period run.
- Display matrix can switch among Total Return, Annualized Return, Sharpe, Max Drawdown, and Annualized Volatility.
- Saves the full combination results to strategy_grid_results.csv.
- Uses the currently selected universe, portfolio selection and trading-cost assumption.
- Signals are computed using full historical data for warm-up, but each calendar-year portfolio return is clipped to that calendar year.

IMPORTANT
This is a robustness research tool, not an automatic parameter optimizer. A single 'best' cell can be overfit. Look for broad parameter regions that behave reasonably across multiple years.

UPGRADE FROM V3.2.2
You can reuse your existing data. Point V3.3.3 Settings to the same permanent data folder (recommended), or copy the complete old data folder into the V3.3.3 folder. Do NOT rebuild historical data merely because of this software upgrade.

WINDOWS
For development mode, use RUN_DEVELOPMENT.bat.
For a portable Windows build, use BUILD_WINDOWS_PORTABLE.bat.


V3.3.3 redesign: the primary robustness view now compares fixed strategies across calendar years and summarizes consistency. The original MOM x rebalance matrix remains as a secondary parameter-sensitivity view.

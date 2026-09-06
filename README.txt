Momentum Lab V3.3.3 - Strategy Grid / Robustness UI Patch

Momentum Lab V3.2.2

ETF patch: ETF research universe is now equity ETFs >= RMB 250M. Bond, money/cash and commodity/gold funds are excluded using Tushare fund_basic classification. Existing V3.2 data is reusable; no historical price rebuild is required.

Momentum Lab V3.2 - Multi-Universe Research Lab

V2 PURPOSE
Momentum Lab V2 adds a proper Data Manager.

You can:
1. Browse to existing local CSI 300 CSV files.
2. Validate that research files contain the required columns.
3. Build the CSI 300 dataset from Tushare from the ground up.
4. Incrementally refresh local data through a selected date.
5. Change the data and result storage folders.
6. Keep Tushare token/proxy in local settings.
7. Run the matched-horizon momentum research and backtest.

FULL BUILD ORDER
- SSE trading calendar
- CSI 300 historical index weights
- daily raw prices for the union of historical CSI 300 constituents
- adjustment factors
- actual daily price limits (stk_limit)
- adjusted price reconstruction

INCREMENTAL REFRESH
V2 checks the latest local dates and downloads the missing period.
CSI300 weights are refreshed with a 120-day overlap so recent index
rebalance snapshots are less likely to be missed.

RESEARCH MODEL
MOM(N) = N-observation adjusted-price return /
         [std(last N daily returns) * sqrt(N)]

Return span and volatility span are locked together.
Rank 1 = strongest.
Q5 = strongest quintile.

PORTABILITY
The final Windows distribution is a folder containing MomentumLab.exe
and its runtime. Copy the whole folder to another Windows PC and run
MomentumLab.exe. The TARGET PC does not need Python installed.

BUILD LIMITATION
This session runs on Linux, so it cannot create a Windows PyInstaller
binary. BUILD_WINDOWS_PORTABLE.bat performs the one-time Windows build.

IMPORTANT
V2 downloads price-limit data in preparation for Realistic Trading Mode,
but the current backtest is still Research Mode (T+1 adjusted close).
Suspension and blocked-order execution logic is the next layer.


V2.1 STARTUP HARDENING
- Correct PyInstaller resource lookup via sys._MEIPASS.
- Verifies ui/index.html exists before starting.
- Automatically chooses another local port if 8765 is occupied.
- Performs a /api/health check before opening the browser.
- Writes startup and runtime errors to logs\momentumlab.log.
- Shows a Windows error dialog for fatal startup problems.
- Includes RUN_DIAGNOSTIC.bat for visible-console troubleshooting.

WHY THIS MATTERS
In V2.0 the packaged EXE could look for ui\index.html beside the EXE,
while PyInstaller keeps bundled resources under its internal runtime
directory. The browser could therefore receive an empty response.
V2.1 separates writable app storage from bundled resource storage.


V2.2 LOCAL DATA LOCATION IMPROVEMENTS
- Hardened Windows OpenFileDialog and forces it to the foreground.
- Added browser-side Import CSV fallback for every data file.
- Imported CSVs are copied into Momentum Lab's configured data folder
  and automatically become the active file location.
- Manual path entry remains supported.

V2.2 RESEARCH DISPLAY
- Shows latest Top 20 momentum tickers.
- Shows rank, momentum score, signal return, signal volatility.
- Shows each ticker's last 10 available adjusted-close observations.
- Shows 10-observation price trend percentage and mini line chart.

NOTE
The "last 10" chart uses the last 10 available stock observations in the
current Research Mode dataset. A later Realistic Trading Mode will use
the exchange trading calendar explicitly for suspension-aware displays.


V2.3 API SPEED / RATE-LIMIT PROTECTION
- Central request governor for every Tushare API call.
- Default safe speed: 240 requests per minute.
- The current proxy error shown by the user indicates a 400/min allowance;
  240/min leaves headroom for network jitter and other requests.
- API speed is configurable in Settings (30-390/min).
- Frequency-limit error 40201 is detected explicitly.
- Automatic cooldown and retry are supported.
- CSV download checkpoints are preserved, so BUILD/UPDATE can resume rather
  than throwing away completed downloads.
- Removed the old 0.03-second loop sleeps; all pacing is now controlled in
  one central limiter.


V2.4 BACKGROUND DATA ENGINE
- Full Build and Update now run in a background thread.
- Browser remains responsive during long Tushare downloads.
- Live job panel shows stage, item progress, elapsed time, recent API request count,
  and API cooldown countdown.
- CANCEL / INTERRUPT button requests a cooperative stop.
- The current API request is allowed to finish, then no new API request is sent.
- Downloaded rows are flushed frequently and persistent checkpoints are saved.
- RESUME restarts the last interrupted/failed job using the checkpoint state.
- Pre-flight validation checks the Tushare token, API URL, date range and writable folders
  before the background job begins.
- Build/Update buttons are disabled while a data job is active.


V2.5 MULTI-SPAN MOMENTUM MONITOR
- Keeps one Primary / Backtest momentum span.
- Adds independent display spans: MOM10, MOM20, MOM40, MOM60, MOM120.
- Primary span continues to drive ranking/backtest. Additional spans are
  diagnostics only and are NOT blended into a composite signal.
- Latest Top 20 table now shows, for each selected display span:
    * momentum score
    * cross-sectional rank
- Adds Span Consensus: number of selected momentum spans where the stock is
  currently ranked Top 20.
- Adds Top20 Count over the primary lookback span.
- Adds Persistence % = Top20 Count / available ranking dates in that span.
- Adds current consecutive Top20 Streak.
- Keeps the 10-observation adjusted-price trend and mini charts.


V2.5 STABLE FIX
- Fixes the null-element JavaScript error in Data Manager.
- Guarantees one live background Job Panel.
- Adds Cancel / Interrupt, Resume, and Reset Stale Job.
- Reset Stale Job refuses to clear a genuinely active worker.
- Normalizes API protection wording to V2.5.
- Retains multi-span MOM monitor and Top20 persistence/streak/consensus.

V3.1 MULTI-UNIVERSE DATA MANAGER
- Four separate data folders: CSI300, CHINEXT_TOP100, STAR_TOP100, ETF_250M.
- Research Universe selector maps only the selected dataset into the common research engine.
- ChiNext/STAR Top100 membership builder uses point-in-time daily_basic total_mv.
- ETF candidates are separate. Historical ≥RMB250m eligibility requires historical size/share evidence; V3.0 does not project today's ETF size backward.
- V2.5 multi-span MOM, persistence, streak, consensus and backtesting are retained.


V3.1 PATCH
- Data Manager now has its own Dataset to Manage selector.
- BUILD / REBUILD acts only on the selected universe.
- Incremental Update acts only on the selected universe.
- Existing file paths are remembered separately per universe.
- CSI300: historical index weights.
- ChiNext Top100 / STAR Top100: month-end point-in-time Top100 by daily_basic.total_mv.
- ETF ≥ RMB250m: month-end historical eligibility from etf_share_size.total_size.
- ETF prices use fund_daily and ETF adjustment factors use fund_adj.
- ETF price-limit download is skipped in research mode.
- Research Lab and Data Manager universe selectors stay synchronized.

IMPORTANT:
ETF historical size endpoint etf_share_size currently requires higher Tushare permissions.
If the account lacks that endpoint, the ETF build will stop with the Tushare permission error.
ChiNext/STAR use daily_basic and normal stock price/factor/limit endpoints.


V3.1.1 RESEARCH WIRING PATCH
- Fixes Research Lab universe selection: /api/run now receives and explicitly loads the selected universe.
- Fixes Primary / Backtest span propagation: the selected MOM10/20/40/60/120 drives signal, ranking, decay and backtest.
- Removes the hard-coded CSI300 name from the signal-decay chart title.
- Adds an explicit RUN identity banner showing universe, MOM span, portfolio, rebalance and cost.
- Coverage line now identifies the dataset, making stale-result mistakes easier to spot.
- Existing V3.1 data can be reused: copy the complete old data folder into the new portable folder before first launch.
- Do NOT Build/Rebuild after copying existing data; validate first, then use Incremental Update only if needed.


V3.2 LOOKBACK COMPARISON UPGRADE
--------------------------------
- Adds Momentum Lookback x Forward Return heatmap for each selected universe.
- Rows are exactly the checked MOM Lookbacks to Compare (MOM10/20/40/60/120).
- Columns are +1D/+3D/+5D/+10D/+20D/+40D/+60D future trading-day horizons.
- Each cell is the average forward return of the selected portfolio (Top N or quintile) ranked by that lookback.
- Primary / Backtest span remains independent and drives the rank-decay diagnostic and portfolio backtest.
- The primary span is NOT silently added to the comparison heatmap when its checkbox is unchecked.
- Universe selection drives all comparison, rank-decay and backtest calculations.
- Existing V3.1/V3.1.1 data can be reused; no historical rebuild is required.


V3.2.2 patch:
- Hard-applies Research Lab Start/End dates to backtest return statistics and portfolio path.
- Displays Dataset Available, Requested, and Backtest Actually Used date ranges separately.
- Adds Annualized Volatility metric to the Research Lab so date-window changes are directly visible.
- Preserves MOM warm-up history before the requested start date for signal calculation only; warm-up returns do not enter portfolio performance.


V3.3.3 changes
- Primary Strategy Grid view is now a cross-year robustness table: one fixed MOM/rebalance strategy per row, calendar-year returns across columns.
- Adds Positive Years, Avg Year, Worst Year, Best Year, Full Return, Full Sharpe, Full Max DD.
- Sorts by consistency first, then full-period Sharpe, instead of highlighting the single best annual cell.
- Keeps MOM x Rebalance heatmap as a secondary sensitivity view.
- No data rebuild required.

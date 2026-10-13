"""Strategy Factor Leading Indicators Study (exploratory, research-only).

READ-ONLY with respect to production. Explores whether observable conditions at a
decision date predict the forward advantage of changing strategy factors over the
production champion MOM10 / Top 10 / 10D ("Mom101010"):

  MOM (M): MOM5 / MOM10 / MOM20 momentum lookback
  N   (N): Top 5 / Top 10 / Top 20 breadth
  H   (H): 5D / 10D rebalance interval

Indicators under test (all point-in-time, computed at the decision signal date t):
  VOL20            CSI 500 (H00905.CSI, total return) trailing 20-session
                   realized volatility, std(daily returns) * sqrt(252)
  Instability5/10  1 - Retention of the MOM10 Top-10 ranking set versus t-5 / t-10
                   trading sessions (trading-session offsets on the shared calendar)
  RS20             CSI 500 20-session total return minus CSI 300 (H00300.CSI)
                   20-session total return

Conventions (identical to the validated adaptive studies):
  * decision grid = champion signal dates cal[first_idx + 10k]; entry = next session
    (T+1); windows span 10 sessions; the 5D arm executes a midpoint rebalance at the
    t+5 signal date (entry cal[p+6]) so both arms are evaluated on identical windows
  * paired forward counterfactuals start from ONE documented initial state: the
    champion's carried portfolio (shares + residual cash) immediately before its
    scheduled entry trade, valued at the entry session with production rules
    (px_val = ffill for suspended names; buys at raw close only when priced)
  * window net return = gross window growth x product(1 - one_way_turnover * rate)
    over the trades executed INSIDE the window only (the next decision's entry cost
    is excluded symmetrically for every arm); rate = 5 bps
  * no equity-curve splicing; isolated paired windows are diagnostics, not a
    deployable strategy; no adaptive rule is constructed or evaluated here

The champion arm reproduces app.backtest() exactly (asserted). Reconciliation against
the 2026-10-10 Strategy Grid run (fingerprint c8ede0...) and the champion archive is
recorded in reconciliation.md. No production file is modified.
"""
import json
import math
import sys
import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import MomentumLabV2 as app
from momentumlab.costs import apply_costs
from momentumlab.benchmark import load_benchmark

UNIVERSE = 'ETF_250M'
COST_BPS = 5.0
COST_RATE = COST_BPS / 10000.0
LOOKBACKS = (5, 10, 20)
BREADTHS = (5, 10, 20)
REBALANCES = (5, 10)
CHAMP_LB, CHAMP_N, CHAMP_RB = 10, 10, 10
GRID_DIR = ROOT / 'results' / 'backtests' / 'ETF_250M_strategy_grid_20261010_210826_8b76b1d6'
CHAMPION_DIR = ROOT / 'results' / 'backtests' / 'ETF_250M_MOM10_20261010_063502_57b6d7e8'
BENCH500_CSV = ROOT / 'data' / 'benchmarks' / 'H00905.CSI.csv'
BENCH300_CSV = ROOT / 'data' / 'benchmarks' / 'H00300.CSI.csv'
VOL_WINDOW = 20
RS_WINDOW = 20
WINDOW_SESSIONS = 10
MID_SESSIONS = 5
BOOT_BLOCK = 3
BOOT_ROUNDS = 2000
BOOT_SEED = 20261010
ATTR_YEAR = '2022'
ATTR_CONFIGS = [(10, 10, 10), (10, 20, 10), (10, 10, 5), (10, 20, 5), (5, 10, 10), (5, 20, 10)]
TS = datetime.now().strftime('%Y%m%d_%H%M%S')
OUT = ROOT / 'results' / 'studies' / 'strategy_factor_leading_indicators' / TS
ART_DIR_REL = 'results/studies/strategy_factor_leading_indicators/' + TS


def cfg_name(lb, n, rb):
    return f'MOM{lb}_Top_{n}_{rb}D'


def log(msg):
    print(msg, flush=True)


def pct(v):
    return 'n/a' if v is None or (isinstance(v, float) and not math.isfinite(v)) else f'{v * 100:+.2f}%'


def num(v, n=4):
    return 'n/a' if v is None or (isinstance(v, float) and not math.isfinite(v)) else f'{v:.{n}f}'


def fin(v):
    return v is not None and isinstance(v, float) and math.isfinite(v)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def at(series, date):
    ts = pd.Timestamp(date)
    if ts in series.index:
        v = series.loc[ts]
        if pd.notna(v):
            return float(v)
    return np.nan


def pearson(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 4:
        return np.nan
    a, b = a[m], b[m]
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a, b):
    s = pd.Series(np.asarray(a, dtype=float))
    r = pd.Series(np.asarray(b, dtype=float))
    m = s.notna() & r.notna() & np.isfinite(s) & np.isfinite(r)
    if m.sum() < 4:
        return np.nan
    return pearson(s[m].rank().values, r[m].rank().values)


def tercile_labels(vals):
    s = pd.Series(np.asarray(vals, dtype=float))
    lab = np.full(len(s), -1)
    ok = s.notna() & np.isfinite(s)
    if ok.sum() < 3:
        return lab
    lab[ok.values] = pd.qcut(s[ok].rank(method='first'), 3, labels=False).astype(int)
    return lab


def mb_idx(n, rng, block=BOOT_BLOCK, rounds=BOOT_ROUNDS):
    nb = int(math.ceil(n / block))
    starts_max = n - block
    for _ in range(rounds):
        starts = rng.integers(0, starts_max + 1, size=nb)
        yield np.concatenate([np.arange(s, s + block) for s in starts])[:n]


def mb_ci(vals, stat_fn, seed=BOOT_SEED):
    x = np.asarray(vals, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2 * BOOT_BLOCK:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    out = []
    for idx in mb_idx(n, rng):
        v = stat_fn(x[idx])
        if v is not None and math.isfinite(v):
            out.append(v)
    if len(out) < BOOT_ROUNDS // 2:
        return (np.nan, np.nan)
    lo, hi = np.percentile(out, [2.5, 97.5])
    return (float(lo), float(hi))


def mb_ci_pairs(x, y, stat_fn, seed=BOOT_SEED):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 2 * BOOT_BLOCK:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    out = []
    for idx in mb_idx(n, rng):
        v = stat_fn(x[idx], y[idx])
        if v is not None and math.isfinite(v):
            out.append(v)
    if len(out) < BOOT_ROUNDS // 2:
        return (np.nan, np.nan)
    lo, hi = np.percentile(out, [2.5, 97.5])
    return (float(lo), float(hi))


def tercile_spread(x, y):
    lab = tercile_labels(x)
    m = (lab >= 0) & np.isfinite(y)
    if m.sum() < 6:
        return np.nan
    lo = np.mean(y[m & (lab == 0)])
    hi = np.mean(y[m & (lab == 2)])
    return float(hi - lo)


def md_table(headers, rows):
    out = ['| ' + ' | '.join(str(h) for h in headers) + ' |',
           '| ' + ' | '.join('---' for _ in headers) + ' |']
    for r in rows:
        out.append('| ' + ' | '.join('' if v is None else str(v) for v in r) + ' |')
    return '\n'.join(out)


# ----------------------------------------------------------------------------------
def build_panel(ranked_d):
    d = ranked_d.sort_values(['trade_date', 'ts_code']).copy()
    cal = pd.DatetimeIndex(sorted(d['trade_date'].dropna().unique()))
    px_raw = d.pivot_table(index='trade_date', columns='ts_code', values='adj_close', aggfunc='last').sort_index()
    px_val = px_raw.ffill()
    return d, cal, px_raw, px_val


def value_state(state, date, px_val):
    cash, shares = state
    v = float(cash)
    for t, sh in shares.items():
        if t in px_val.columns:
            p = px_val.at[date, t]
            if pd.notna(p):
                v += sh * p
    return v


def trade_state(state, date, tickers, px_raw, px_val):
    """Verbatim production entry mechanics (gross of cost) from an arbitrary state.

    Returns (new_state, pre_trade_value, one_way_turnover). Matches backtest() lines
    2046-2068: value via px_val, sell marks via px_val, buys at raw close only when
    priced, equal-weight targets, residual cash retained.
    """
    cash, shares = state
    before = value_state(state, date, px_val)
    if not tickers or before <= 0:
        return (cash, dict(shares)), before, np.nan
    current = {}
    for t, sh in shares.items():
        p = px_val.at[date, t] if t in px_val.columns else np.nan
        current[t] = sh * p if pd.notna(p) else 0.0
    target_each = before / len(tickers)
    traded = sum(abs((target_each if t in tickers else 0) - current.get(t, 0)) for t in set(current) | set(tickers))
    tow = 0.5 * traded / before
    new = {}
    for t in tickers:
        p = px_raw.at[date, t]
        if p > 0:
            new[t] = target_each / p
    new_cash = before - sum(new[t] * px_raw.at[date, t] for t in new)
    return (new_cash, new), before, tow


def run_arm(s0, legs, exit_date, px_raw, px_val):
    """One paired window for one arm: sequential trades from the common initial state."""
    v_start = value_state(s0, legs[0][0], px_val) if legs else np.nan
    state = s0
    factors = 1.0
    traded_flags = []
    for date, tickers in legs:
        state, before, tow = trade_state(state, date, tickers, px_raw, px_val)
        if fin(tow):
            factors *= (1 - tow * COST_RATE)
            traded_flags.append(tow)
    v_end = value_state(state, exit_date, px_val)
    gross = v_end / v_start - 1 if v_start and v_start > 0 else np.nan
    net = (1 + gross) * factors - 1 if fin(gross) else np.nan
    return gross, net, len(traded_flags)


def tickers_for(ranked_d, selection, signal_date, exec_date, px_raw):
    chosen = app.select_target_rows(ranked_d, selection, signal_date)
    return [t for t in chosen['ts_code'] if t in px_raw.columns and pd.notna(px_raw.at[exec_date, t])]


def top_set(ranked_d, date, n):
    rows = ranked_d[(ranked_d['trade_date'] == pd.Timestamp(date)) &
                    (ranked_d['csi300_member'] == 1) & ranked_d['momentum_rank'].notna()]
    return set(rows.loc[rows['momentum_rank'] <= n, 'ts_code'].astype(str)), len(rows)


def yearly_returns(curve):
    out = {}
    for y in sorted({d.year for d in curve.index}):
        vals = curve[curve.index.year == y]
        if vals.empty:
            continue
        prior = curve[curve.index < vals.index[0]]
        base = prior.iloc[-1] if len(prior) else 1.0
        out[str(y)] = float(vals.iloc[-1] / base - 1)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reconciliation = []

    def recon(check, status, detail):
        reconciliation.append(dict(check=check, status=status, detail=detail))
        log(f"      [{'PASS' if status == 'PASS' else status}] {check}" + (f" — {detail}" if detail else ''))

    # ------------------------------------------------------------------ [1] data
    log('[1/10] Loading dataset, integrity manifest and benchmarks ...')
    cfg = app.load_config()
    paths = app.selected_universe_paths(cfg, UNIVERSE)
    integrity = app.validate_research_files(UNIVERSE, mode='cached')
    integrity_status = integrity.get('status') or ('ok' if integrity.get('ok') else 'unknown')
    manifest = integrity.get('manifest') or {}
    fingerprint = manifest.get('dataset_fingerprint')
    completeness = integrity.get('completeness') or {}
    prices = app.research_prices(paths)
    weights = app.read_csv(paths['weights_csv'])
    ranked = {}
    for lb in LOOKBACKS:
        ranked[lb] = app.merge_membership_and_rank(app.calculate_signal(prices, lb), weights)
        log(f"      ranked MOM{lb}: rows={len(ranked[lb]):,} dates={ranked[lb]['trade_date'].nunique():,}")

    bench500, meta500 = load_benchmark(str(BENCH500_CSV), 'H00905.CSI', 'CSI 500 Total Return Index', basis='total_return')
    bench300, meta300 = load_benchmark(str(BENCH300_CSV), 'H00300.CSI', 'CSI 300 Total Return Index', basis='total_return')
    r500 = bench500.pct_change(fill_method=None)
    vol20 = (r500.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1) * np.sqrt(252))
    rs20 = (bench500 / bench500.shift(RS_WINDOW) - 1) - (bench300 / bench300.shift(RS_WINDOW) - 1)
    log(f"      benchmarks: CSI500 {bench500.index.min():%Y-%m-%d}..{bench500.index.max():%Y-%m-%d}, "
        f"CSI300 {bench300.index.min():%Y-%m-%d}..{bench300.index.max():%Y-%m-%d}")

    grid = pd.read_csv(GRID_DIR / 'strategy_grid_results.csv')
    grid_meta = json.loads((GRID_DIR / 'run_metadata.json').read_text(encoding='utf-8'))
    grid_fp = (grid_meta.get('provenance') or {}).get('dataset_fingerprint')
    log(f"      grid rows={len(grid)}  dataset fingerprint={fingerprint}  grid fingerprint={grid_fp}")

    # ------------------------------------------- [2] continuous 18-config sims
    log('[2/10] Running the 18 continuous configs via production backtest() ...')
    curves = {}
    for lb in LOOKBACKS:
        for n in BREADTHS:
            for rb in REBALANCES:
                key = cfg_name(lb, n, rb)
                eq, dd, hist, gstats = app.backtest(ranked[lb], f'Top {n}', rb, None, None, 0)
                net, net_dd, hist2, nstats = apply_costs(eq, hist, gstats, COST_BPS)
                curves[key] = dict(eq=eq, net=net, hist=hist, gstats=gstats, nstats=nstats)
        log(f"      MOM{lb}: 6 configs done")

    # reconciliation vs grid Full Period net curves (all 18)
    grid_by_key = {}
    for row in grid.itertuples():
        grid_by_key[(str(row.period), int(row.lookback), str(row.selection).replace(' ', ''), int(row.rebalance_days))] = row
    all_ok = True
    for lb in LOOKBACKS:
        for n in BREADTHS:
            for rb in REBALANCES:
                key = cfg_name(lb, n, rb)
                cell = grid_by_key.get(('Full Period', lb, f'Top{n}', rb))
                if cell is None:
                    recon(f'grid row exists for {key} Full Period', 'FAIL', 'missing row')
                    all_ok = False
                    continue
                ref = pd.read_csv(GRID_DIR / f'Full_Period_{key}_net_nav.csv', index_col=0).iloc[:, 0]
                ref.index = pd.to_datetime(ref.index)
                mine = curves[key]['net']
                idx_ok = ref.index.equals(mine.index)
                val_ok = idx_ok and bool(np.allclose(ref.values, mine.values, rtol=0, atol=1e-9))
                if not (idx_ok and val_ok):
                    all_ok = False
                    recon(f'{key} continuous net curve == grid Full Period net_nav.csv', 'FAIL',
                          f'index_match={idx_ok} values_match={val_ok}')
    recon('all 18 continuous configs reproduce grid Full Period net curves (fingerprint-matched)',
          'PASS' if all_ok else 'FAIL',
          f'grid={GRID_DIR.name}, fingerprint {grid_fp}' + ('' if grid_fp == fingerprint else ' — MISMATCH vs current fingerprint'))

    # reconciliation vs champion archive (MOM10/Top10/10D)
    champ_meta = json.loads((CHAMPION_DIR / 'run_metadata.json').read_text(encoding='utf-8'))
    champ_stats = champ_meta.get('stats', {})
    champ_fp = (champ_meta.get('provenance') or {}).get('dataset_fingerprint')
    eq_csv = pd.read_csv(CHAMPION_DIR / 'equity.csv')
    arch = pd.Series(eq_csv.iloc[:, 1].values, index=pd.to_datetime(eq_csv.iloc[:, 0]), name='net_nav')
    mine_champ = curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['net']
    champ_ok = arch.index.equals(mine_champ.index) and bool(np.allclose(arch.values, mine_champ.values, rtol=0, atol=1e-9))
    nstats = curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['nstats']
    stat_keys = ['net_return', 'net_cagr', 'net_max_drawdown', 'total_turnover', 'annualized_turnover']
    stat_ok = all(fin(nstats.get(k)) and abs(nstats[k] - float(champ_stats[k])) <= 1e-12 * max(1.0, abs(float(champ_stats[k])))
                  for k in stat_keys if champ_stats.get(k) is not None)
    recon('champion continuous net curve == champion archive equity.csv (20261010_063502)',
          'PASS' if champ_ok else 'FAIL', f'archive fingerprint {champ_fp}')
    recon('champion continuous net stats == champion archive run_metadata.json',
          'PASS' if stat_ok else 'FAIL', 'net_return/net_cagr/net_maxDD/turnover compared')

    # ------------------------------------------- [3] stateful champion simulation
    log('[3/10] Stateful champion simulation (verbatim production loop, share states) ...')
    d_champ, cal, px_raw, px_val = build_panel(ranked[CHAMP_LB])
    valid_dates = d_champ.loc[(d_champ['csi300_member'] == 1) & d_champ['momentum_rank'].notna(), 'trade_date']
    first_v, last_v = valid_dates.min(), valid_dates.max()
    candidates = cal[(cal >= first_v) & (cal <= last_v)]
    first_idx = cal.get_loc(candidates[0])
    sig_idx = [i for i in range(first_idx, len(cal) - 1, CHAMP_RB) if cal[i] <= last_v]
    sig_to_entry = {cal[i + 1]: cal[i] for i in sig_idx}

    cash, shares = 1.0, {}
    nav, states, sim_hist = [], {}, []
    for di in range(sig_idx[0] + 1, len(cal)):
        date = cal[di]
        before = value_state((cash, shares), date, px_val)
        if date in sig_to_entry:
            sig = sig_to_entry[date]
            chosen = app.select_target_rows(d_champ, f'Top {CHAMP_N}', sig).copy()
            tickers = [t for t in chosen['ts_code'] if t in px_raw.columns and pd.notna(px_raw.at[date, t])]
            if tickers:
                pre_cash, pre_shares = float(cash), dict(shares)
                current = {}
                for t, sh in shares.items():
                    p = px_val.at[date, t] if t in px_val.columns else np.nan
                    current[t] = sh * p if pd.notna(p) else 0.0
                target_each = before / len(tickers)
                detail = app.compare_weights({t: v / before for t, v in current.items()} if before > 0 else {},
                                             {t: 1 / len(tickers) for t in tickers}, initial=not sim_hist)
                traded = sum(abs((target_each if t in tickers else 0) - current.get(t, 0))
                             for t in set(current) | set(tickers))
                new = {}
                for t in tickers:
                    p = px_raw.at[date, t]
                    if p > 0:
                        new[t] = target_each / p
                cash = before - sum(new[t] * px_raw.at[date, t] for t in new)
                shares = new
                tow_v = 0.5 * traded / before if before > 0 else np.nan
                states[date] = dict(signal=sig, pre_cash=pre_cash, pre_shares=pre_shares, pre_value=before,
                                    post_cash=float(cash), post_shares=dict(shares), tickers=tickers,
                                    tow=tow_v, names=len(tickers))
                sim_hist.append({"signal_date": sig.strftime('%Y-%m-%d'), "entry_date": date.strftime('%Y-%m-%d'),
                                 "names": len(tickers), "one_way_turnover": tow_v, "cost": 0.0,
                                 "tickers": ','.join(tickers), **detail})
        nav.append((date, value_state((cash, shares), date, px_val)))
    s_nav = pd.Series([v for _, v in nav], index=pd.DatetimeIndex([d for d, _ in nav]), name='nav')
    sim_eq = s_nav / s_nav.iloc[0]
    sim_hist = pd.DataFrame(sim_hist)

    prod_eq = curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['eq']
    eq_ok = sim_eq.index.equals(prod_eq.index) and bool(np.allclose(sim_eq.values, prod_eq.values, rtol=0, atol=1e-12))
    hist_prod = curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['hist']
    hist_ok = (len(sim_hist) == len(hist_prod) and
               sim_hist['entry_date'].tolist() == hist_prod['entry_date'].tolist() and
               np.allclose(sim_hist['one_way_turnover'].astype(float).values,
                           hist_prod['one_way_turnover'].astype(float).values, rtol=0, atol=1e-12))
    recon('stateful champion simulator == production backtest() (curve + trade log)',
          'PASS' if (eq_ok and hist_ok) else 'FAIL', f'curve={eq_ok} log={hist_ok}')
    if not (eq_ok and hist_ok):
        raise RuntimeError('Stateful champion simulation does not reproduce production backtest(); stopping.')

    # ------------------------------------------- [4] paired decision windows
    log('[4/10] Building paired forward counterfactuals on the 10-session decision grid ...')
    decisions = []
    p = first_idx
    while p + WINDOW_SESSIONS + 1 <= len(cal) - 1:
        decisions.append(p)
        p += WINDOW_SESSIONS
    log(f"      decisions={len(decisions)}  {cal[decisions[0]]:%Y-%m-%d} .. {cal[decisions[-1]]:%Y-%m-%d}")

    rows_fa = []
    champ_dev = 0.0
    for p in decisions:
        t, e, m, x = cal[p], cal[p + 1], cal[p + 6], cal[p + 11]
        st = states.get(e)
        if st is None:
            prev = max([dd for dd in states if dd < e], default=None)
            s0 = (states[prev]['post_cash'], states[prev]['post_shares']) if prev is not None else (1.0, {})
            champ_tickers = []
            champ_tow_ref = np.nan
        else:
            s0 = (states[e]['pre_cash'], states[e]['pre_shares'])
            champ_tickers = st['tickers']
            champ_tow_ref = st['tow']
        v0 = value_state(s0, e, px_val)

        # champion arm (reproduces production exactly)
        cg, cn, ctr = run_arm(s0, [(e, champ_tickers)], x, px_raw, px_val)
        ref_gross = float(curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['eq'].at[x] /
                          curves[cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)]['eq'].at[e] - 1)
        champ_dev = max(champ_dev, abs(cg - ref_gross))

        # arm A: MOM10 Top20 10D
        tk_a = tickers_for(ranked[CHAMP_LB], 'Top 20', t, e, px_raw)
        ag, an, _ = run_arm(s0, [(e, tk_a)], x, px_raw, px_val)

        # arm B: MOM10 Top10 5D (midpoint trade at t+5 signal, entry cal[p+6])
        tk_mid = tickers_for(ranked[CHAMP_LB], 'Top 10', cal[p + 5], m, px_raw)
        bg, bn, _ = run_arm(s0, [(e, champ_tickers), (m, tk_mid)], x, px_raw, px_val)

        # arm C: MOM5 Top10 10D
        tk_c = tickers_for(ranked[5], 'Top 10', t, e, px_raw)
        c5g, c5n, _ = run_arm(s0, [(e, tk_c)], x, px_raw, px_val)

        # arm C2: MOM5 Top20 10D (compared against arm A)
        tk_c2 = tickers_for(ranked[5], 'Top 20', t, e, px_raw)
        c2g, c2n, _ = run_arm(s0, [(e, tk_c2)], x, px_raw, px_val)

        set5, _ = top_set(ranked[5], t, 10)
        set10, n10 = top_set(ranked[CHAMP_LB], t, 10)
        if p - 5 >= 0:
            set10_5, n10_5 = top_set(ranked[CHAMP_LB], cal[p - 5], 10)
            instab5 = 1 - len(set10 & set10_5) / 10 if (n10 and n10_5) else np.nan
        else:
            instab5 = np.nan
        if p - 10 >= 0:
            set10_10, n10_10 = top_set(ranked[CHAMP_LB], cal[p - 10], 10)
            instab10 = 1 - len(set10 & set10_10) / 10 if (n10 and n10_10) else np.nan
        else:
            instab10 = np.nan

        rows_fa.append(dict(
            signal_date=t, entry_date=e, mid_signal_date=cal[p + 5], exit_date=x,
            s0_value=v0, champ_gross=cg, champ_net=cn,
            a_gross=ag, a_net=an, a_names=len(tk_a),
            b_gross=bg, b_net=bn, b_mid_names=len(tk_mid), b_mid_no_trade=len(tk_mid) == 0,
            c_gross=c5g, c_net=c5n, c_names=len(tk_c),
            c2_gross=c2g, c2_net=c2n, c2_names=len(tk_c2),
            adv_a_gross=ag - cg, adv_a_net=an - cn,
            adv_b_gross=bg - cg, adv_b_net=bn - cn,
            adv_c_gross=c5g - cg, adv_c_net=c5n - cn,
            adv_c2_gross=c2g - ag, adv_c2_net=c2n - an,
            vol20=at(vol20, t), instability5=instab5, instability10=instab10, rs20=at(rs20, t),
            champ_names=len(champ_tickers), champ_name_count=len(set10),
            m5_overlap=len(set5 & set10), year=t.year))
    fa = pd.DataFrame(rows_fa)
    recon('champion arm reproduces champion curve ratio on every window',
          'PASS' if champ_dev <= 1e-9 else 'FAIL', f'max |deviation|={champ_dev:.2e}')
    if champ_dev > 1e-9:
        raise RuntimeError('Champion paired arm deviates from the production champion curve; stopping.')

    # ------------------------------------------- [5] indicator events + diagnostics
    log('[5/10] Writing forward advantages and indicator events ...')
    comparisons = [
        ('A', 'MOM10/Top20/10D minus MOM10/Top10/10D', 'adv_a_net', 'adv_a_gross'),
        ('B', 'MOM10/Top10/5D minus MOM10/Top10/10D', 'adv_b_net', 'adv_b_gross'),
        ('C', 'MOM5/Top10/10D minus MOM10/Top10/10D', 'adv_c_net', 'adv_c_gross'),
        ('C2', 'MOM5/Top20/10D minus MOM10/Top20/10D', 'adv_c2_net', 'adv_c2_gross'),
    ]
    indicators = [('VOL20', 'vol20'), ('Instability5', 'instability5'), ('Instability10', 'instability10'), ('RS20', 'rs20')]
    role_map = {
        ('VOL20', 'A'): 'primary', ('Instability5', 'B'): 'primary', ('RS20', 'C'): 'primary',
        ('Instability10', 'B'): 'secondary', ('RS20', 'C2'): 'secondary',
    }
    ev_rows = []
    for ind_name, col in indicators:
        for comp, comp_label, net_col, gross_col in comparisons:
            role = role_map.get((ind_name, comp), 'cross-check')
            for r in fa.itertuples():
                ev_rows.append(dict(indicator=ind_name, comparison=comp, comparison_label=comp_label,
                                    role=role, signal_date=r.signal_date, entry_date=r.entry_date,
                                    exit_date=r.exit_date, indicator_value=getattr(r, col),
                                    adv_net=getattr(r, net_col), adv_gross=getattr(r, gross_col)))
    ev = pd.DataFrame(ev_rows)
    ev.to_csv(OUT / 'indicator_events.csv', index=False, encoding='utf-8-sig')

    fa_out = fa.rename(columns={'signal_date': 'signal_date', 'entry_date': 'entry_date'})
    fa_out.to_csv(OUT / 'forward_advantages.csv', index=False, encoding='utf-8-sig')

    diag_rows = []
    for ind_name, col in indicators:
        for comp, comp_label, net_col, gross_col in comparisons:
            sub = ev[(ev['indicator'] == ind_name) & (ev['comparison'] == comp)]
            xv = sub['indicator_value'].values.astype(float)
            yv = sub['adv_net'].values.astype(float)
            gv = sub['adv_gross'].values.astype(float)
            mask = np.isfinite(xv) & np.isfinite(yv)
            n = int(mask.sum())
            if n < 10:
                diag_rows.append(dict(indicator=ind_name, comparison=comp, comparison_label=comp_label,
                                      role=role_map.get((ind_name, comp), 'cross-check'), n_events=n,
                                      notes='insufficient indicator coverage'))
                continue
            xm, ym, gm = xv[mask], yv[mask], gv[mask]
            lab = tercile_labels(xm)
            t_means, t_hits = {}, {}
            for k in range(3):
                mk = lab == k
                t_means[k] = float(np.mean(ym[mk])) if mk.any() else np.nan
                t_hits[k] = float(np.mean(ym[mk] > 0)) if mk.any() else np.nan
            mean_ci = mb_ci(ym, lambda z: float(np.mean(z)))
            rho_ci = mb_ci_pairs(xm, ym, lambda a, b: spearman(a, b))
            spread_ci = mb_ci_pairs(xm, ym, tercile_spread)
            years = sub['signal_date'].dt.year.values[mask]
            annual = {}
            for y in sorted(set(years)):
                my = years == y
                if my.sum() >= 3:
                    annual[str(int(y))] = float(np.mean(ym[my]))
            y22 = np.array([str(int(v)) == ATTR_YEAR for v in years])
            m22 = float(np.mean(ym[y22])) if y22.any() else np.nan
            mex = float(np.mean(ym[~y22])) if (~y22).any() else np.nan
            absy = np.abs(ym)
            if n >= 20:
                cut = np.percentile(absy, 95)
                keep = absy < cut
                drop_mean = float(np.mean(ym[keep])) if keep.any() else np.nan
            else:
                drop_mean = np.nan
            diag_rows.append(dict(
                indicator=ind_name, comparison=comp, comparison_label=comp_label,
                role=role_map.get((ind_name, comp), 'cross-check'),
                n_events=n, coverage_pct=round(100.0 * n / len(fa), 1),
                mean_adv_net=float(np.mean(ym)), median_adv_net=float(np.median(ym)),
                mean_adv_gross=float(np.mean(gm)),
                win_rate_net=float(np.mean(ym > 0)),
                pearson=pearson(xm, ym), spearman=spearman(xm, ym),
                boot_mean_lo=mean_ci[0], boot_mean_hi=mean_ci[1],
                boot_spearman_lo=rho_ci[0], boot_spearman_hi=rho_ci[1],
                tercile_low_mean=t_means[0], tercile_mid_mean=t_means[1], tercile_high_mean=t_means[2],
                tercile_high_minus_low=t_means[2] - t_means[0],
                boot_spread_lo=spread_ci[0], boot_spread_hi=spread_ci[1],
                tercile_low_hit=t_hits[0], tercile_mid_hit=t_hits[1], tercile_high_hit=t_hits[2],
                annual_means=json.dumps(annual), mean_2022=m22, mean_ex2022=mex,
                mean_drop_extreme=drop_mean,
                mean_cost_effect=float(np.mean(gm) - np.mean(ym)),
                max_adv_net=float(np.max(ym)), min_adv_net=float(np.min(ym)),
                notes=''))
    diag = pd.DataFrame(diag_rows)
    diag.to_csv(OUT / 'predictive_diagnostics.csv', index=False, encoding='utf-8-sig')

    # hypothesis classification (descriptive aid, stated rules in report)
    classifications = {}
    for hyp, pair in (('A', ('VOL20', 'A')), ('B', ('Instability5', 'B')), ('C', ('RS20', 'C'))):
        row = diag[(diag['indicator'] == pair[0]) & (diag['comparison'] == pair[1])]
        if row.empty or int(row.iloc[0]['n_events']) < 10:
            classifications[hyp] = dict(indicator=pair[0], status='Unassessable',
                                        detail='insufficient indicator coverage')
            continue
        r0 = row.iloc[0]
        ci_excl0 = fin(r0['boot_mean_lo']) and fin(r0['boot_mean_hi']) and (r0['boot_mean_lo'] > 0 or r0['boot_mean_hi'] < 0)
        annual = json.loads(r0['annual_means']) if isinstance(r0['annual_means'], str) else {}
        if annual:
            sign_frac = float(np.mean([np.sign(v) == np.sign(r0['mean_adv_net']) for v in annual.values()]))
        else:
            sign_frac = np.nan
        spread = r0['tercile_high_minus_low']
        monotone = fin(spread) and np.sign(spread) == np.sign(r0['spearman']) and abs(spread) >= 0.005
        points = int(ci_excl0) + int(fin(sign_frac) and sign_frac >= 5 / 6) + int(monotone)
        if points >= 3:
            status = 'Supported for further prospective validation (in-sample only)'
        elif points == 0:
            status = 'Not supported'
        else:
            status = 'Inconclusive'
        classifications[hyp] = dict(
            indicator=pair[0], comparison=pair[1], status=status,
            detail=f"n={int(r0['n_events'])} mean_adv_net={num(r0['mean_adv_net'])} win={num(r0['win_rate_net'],3)} "
                   f"spearman={num(r0['spearman'],3)} CI_mean=[{num(r0['boot_mean_lo'])},{num(r0['boot_mean_hi'])}] "
                   f"tercile_hi-lo={num(spread)} year_sign_frac={num(sign_frac,2)}")
        log(f"      Hypothesis {hyp}: {status} — {classifications[hyp]['detail']}")

    # ------------------------------------------- predictive chart
    plt = app.plotting()
    prim = [('VOL20', 'A', 'Forward advantage: MOM10/Top20/10D minus champion (net, 10 sessions)'),
            ('Instability5', 'B', 'Forward advantage: MOM10/Top10/5D minus champion (net, 10 sessions)'),
            ('RS20', 'C', 'Forward advantage: MOM5/Top10/10D minus champion (net, 10 sessions)')]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    for ax, (ind_name, comp, ttl) in zip(axes, prim):
        sub = ev[(ev['indicator'] == ind_name) & (ev['comparison'] == comp)].dropna(subset=['indicator_value', 'adv_net'])
        xv, yv = sub['indicator_value'].values, sub['adv_net'].values * 100
        ax.scatter(xv, yv, s=14, alpha=0.55, color='#37618e')
        lab = tercile_labels(xv)
        for k, colr in ((0, '#c0392b'), (1, '#7f8c8d'), (2, '#27ae60')):
            mk = lab == k
            if mk.any():
                ax.scatter([np.mean(xv[mk])], [np.mean(yv[mk])], s=160, marker='D', color=colr,
                           edgecolor='black', zorder=5,
                           label=f'tercile {["low","mid","high"][k]}: mean {np.mean(yv[mk]):+.2f}%')
        ax.axhline(0, color='black', lw=1)
        row = diag[(diag['indicator'] == ind_name) & (diag['comparison'] == comp)].iloc[0]
        ax.set_title(f"{ind_name} vs comparison {comp}\nn={int(row['n_events'])}, spearman={num(row['spearman'],3)}, "
                     f"mean={num(row['mean_adv_net'],4)}", fontsize=9)
        ax.set_xlabel(ind_name)
        ax.set_ylabel('forward net advantage (%)')
        ax.legend(fontsize=7, loc='best')
        ax.grid(alpha=0.25)
    fig.suptitle('Leading indicator values at decision date t vs paired forward net advantage over the next 10 sessions', fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / 'chart_indicator_forward_advantage.png', dpi=160, bbox_inches='tight')
    plt.close(fig)

    # ------------------------------------------- [6] factor interactions
    log('[6/10] Computing factor interactions (grid standalone years + continuous curve) ...')
    pairs_spec = []
    for n in BREADTHS:
        for rb in REBALANCES:
            pairs_spec.append(('MOM', f'MOM5-MOM10 @ Top {n}, {rb}D', cfg_name(5, n, rb), cfg_name(10, n, rb)))
            pairs_spec.append(('MOM', f'MOM20-MOM10 @ Top {n}, {rb}D', cfg_name(20, n, rb), cfg_name(10, n, rb)))
    for lb in LOOKBACKS:
        for rb in REBALANCES:
            pairs_spec.append(('breadth', f'Top20-Top10 @ MOM{lb}, {rb}D', cfg_name(lb, 20, rb), cfg_name(lb, 10, rb)))
            pairs_spec.append(('breadth', f'Top5-Top10 @ MOM{lb}, {rb}D', cfg_name(lb, 5, rb), cfg_name(lb, 10, rb)))
    for lb in LOOKBACKS:
        for n in BREADTHS:
            pairs_spec.append(('rebalance', f'5D-10D @ MOM{lb}, Top {n}', cfg_name(lb, n, 5), cfg_name(lb, n, 10)))

    periods = [str(y) for y in sorted({int(r) for r in grid['period'] if str(r).isdigit()})] + ['Full Period']
    cont_year = {k: dict(net=yearly_returns(v['net']), gross=yearly_returns(v['eq'])) for k, v in curves.items()}

    def delta(basis, period, challenger, champion):
        if basis == 'grid_standalone_year':
            def read(cfgk):
                lb, n, rb = parse_cfg(cfgk)
                row = grid_by_key.get((period, lb, f'Top{n}', rb))
                return (float(row.net_return), float(row.gross_return)) if row is not None else (np.nan, np.nan)
            cn, cg = read(challenger)
            hn, hg = read(champion)
            return cn - hn, cg - hg
        else:
            if period == 'Full Period':
                return (curves[challenger]['nstats']['net_return'] - curves[champion]['nstats']['net_return'],
                        curves[challenger]['gstats']['total_return'] - curves[champion]['gstats']['total_return'])
            cy = cont_year[challenger]
            hy = cont_year[champion]
            return (cy['net'].get(period, np.nan) - hy['net'].get(period, np.nan),
                    cy['gross'].get(period, np.nan) - hy['gross'].get(period, np.nan))

    def parse_cfg(k):
        body = k[3:]
        lb_str, rest = body.split('_Top_')
        n_str, rb_str = rest.split('_')
        return int(lb_str), int(n_str), int(rb_str.rstrip('D'))

    inter_rows = []
    D = {}
    for basis in ('grid_standalone_year', 'continuous_curve'):
        for period in periods:
            for axis, label, chall, champ in pairs_spec:
                dn, dg = delta(basis, period, chall, champ)
                D[(basis, period, label)] = (dn, dg)
                inter_rows.append(dict(basis=basis, period=period, axis=axis, comparison=label,
                                       delta_net=dn, delta_gross=dg))

    inter_specs = []
    for lb in (5, 20):
        for n in BREADTHS:
            inter_specs.append(('interaction_MxD', f'MOM{lb}-MOM10: (5D-10D contrast) @ Top {n}',
                                [(1, f'MOM{lb}-MOM10 @ Top {n}, 5D'), (-1, f'MOM{lb}-MOM10 @ Top {n}, 10D')]))
    for nn in (20, 5):
        for lb in LOOKBACKS:
            inter_specs.append(('interaction_NxD', f'Top{nn}-Top10: (5D-10D contrast) @ MOM{lb}',
                                [(1, f'Top{nn}-Top10 @ MOM{lb}, 5D'), (-1, f'Top{nn}-Top10 @ MOM{lb}, 10D')]))
    for lb in (5, 20):
        for rb in REBALANCES:
            inter_specs.append(('interaction_MxN', f'MOM{lb}-MOM10: (Top20-Top10 contrast) @ {rb}D',
                                [(1, f'MOM{lb}-MOM10 @ Top 20, {rb}D'), (-1, f'MOM{lb}-MOM10 @ Top 10, {rb}D')]))
    for nn in (20, 5):
        for rb in REBALANCES:
            inter_specs.append(('interaction_MxN', f'Top{nn}-Top10: (MOM5-MOM10 contrast) @ {rb}D',
                                [(1, f'Top{nn}-Top10 @ MOM5, {rb}D'), (-1, f'Top{nn}-Top10 @ MOM10, {rb}D')]))
    for lb in (5, 20):
        inter_specs.append(('interaction_three_way', f'MOM{lb}-MOM10: three-way (5D-10D | Top 20 minus | Top 10)',
                            [(1, f'MOM{lb}-MOM10 @ Top 20, 5D'), (-1, f'MOM{lb}-MOM10 @ Top 20, 10D'),
                             (-1, f'MOM{lb}-MOM10 @ Top 10, 5D'), (1, f'MOM{lb}-MOM10 @ Top 10, 10D')]))
    for nn in (20, 5):
        inter_specs.append(('interaction_three_way', f'Top{nn}-Top10: three-way (MOM5-MOM10 | 5D minus | 10D)',
                            [(1, f'Top{nn}-Top10 @ MOM5, 5D'), (-1, f'Top{nn}-Top10 @ MOM5, 10D'),
                             (-1, f'Top{nn}-Top10 @ MOM10, 5D'), (1, f'Top{nn}-Top10 @ MOM10, 10D')]))

    for basis in ('grid_standalone_year', 'continuous_curve'):
        for period in periods:
            for axis, label, terms in inter_specs:
                dn = sum(sg * D[(basis, period, t)][0] for sg, t in terms)
                dg = sum(sg * D[(basis, period, t)][1] for sg, t in terms)
                inter_rows.append(dict(basis=basis, period=period, axis=axis, comparison=label,
                                       delta_net=dn, delta_gross=dg))
    inter = pd.DataFrame(inter_rows)
    inter.to_csv(OUT / 'factor_interactions.csv', index=False, encoding='utf-8-sig')

    wide_rows = []
    for key in sorted(curves):
        for basis in ('grid_standalone_year', 'continuous_curve'):
            row = dict(config=key, basis=basis)
            for period in periods:
                lb, n, rb = parse_cfg(key)
                if basis == 'grid_standalone_year':
                    g = grid_by_key.get((period, lb, f'Top{n}', rb))
                    row[f'{period}_net'] = float(g.net_return) if g is not None else np.nan
                    row[f'{period}_gross'] = float(g.gross_return) if g is not None else np.nan
                else:
                    if period == 'Full Period':
                        row[f'{period}_net'] = curves[key]['nstats']['net_return']
                        row[f'{period}_gross'] = curves[key]['gstats']['total_return']
                    else:
                        row[f'{period}_net'] = cont_year[key]['net'].get(period, np.nan)
                        row[f'{period}_gross'] = cont_year[key]['gross'].get(period, np.nan)
            wide_rows.append(row)
    pd.DataFrame(wide_rows).to_csv(OUT / 'config_period_returns.csv', index=False, encoding='utf-8-sig')

    # interplay charts
    primary_labels = [
        ('MOM5-MOM10 @ Top 10, 10D', '#c0392b'),
        ('MOM20-MOM10 @ Top 10, 10D', '#e67e22'),
        ('Top20-Top10 @ MOM10, 10D', '#27ae60'),
        ('Top5-Top10 @ MOM10, 10D', '#16a085'),
        ('5D-10D @ MOM10, Top 10', '#37618e'),
    ]
    annual_periods = [pp for pp in periods if pp != 'Full Period']
    fig = plt.figure(figsize=(15, 12))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 2.6], hspace=0.28, wspace=0.16)
    ax1 = fig.add_subplot(gs[0, 0])
    for label, colr in primary_labels:
        ys = [D[('grid_standalone_year', pp, label)][0] * 100 for pp in annual_periods]
        ax1.plot(annual_periods, ys, marker='o', label=label, color=colr)
    ax1.axhline(0, color='black', lw=1)
    ax1.set_title('Primary factor deltas by year — net, grid standalone-year convention (%)', fontsize=9)
    ax1.legend(fontsize=7)
    ax1.grid(alpha=0.25)
    ax2 = fig.add_subplot(gs[0, 1])
    xs = np.arange(len(primary_labels))
    fp_net = [D[('grid_standalone_year', 'Full Period', label)][0] * 100 for label, _ in primary_labels]
    fp_gross = [D[('grid_standalone_year', 'Full Period', label)][1] * 100 for label, _ in primary_labels]
    ax2.bar(xs - 0.19, fp_net, width=0.38, label='net', color='#37618e')
    ax2.bar(xs + 0.19, fp_gross, width=0.38, label='gross', color='#95a5a6')
    for xi, (vn, vg) in enumerate(zip(fp_net, fp_gross)):
        ax2.text(xi - 0.19, vn, f'{vn:+.0f}', ha='center', va='bottom' if vn >= 0 else 'top', fontsize=7)
        ax2.text(xi + 0.19, vg, f'{vg:+.0f}', ha='center', va='bottom' if vg >= 0 else 'top', fontsize=7)
    ax2.axhline(0, color='black', lw=1)
    ax2.set_xticks(xs, [l.split(' @ ')[0] for l, _ in primary_labels], fontsize=8)
    ax2.set_title('Full Period deltas — grid standalone convention (cumulative, %)', fontsize=9)
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.25, axis='y')
    ax3 = fig.add_subplot(gs[1, :])
    hm_rows = [label for _, label, _, _ in pairs_spec]
    hm = np.array([[D[('grid_standalone_year', pp, label)][0] * 100 for pp in annual_periods] for label in hm_rows])
    vmax = np.nanmax(np.abs(hm)) if np.isfinite(hm).any() else 1.0
    try:
        from matplotlib.colors import TwoSlopeNorm
        norm = TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)
        im = ax3.imshow(hm, aspect='auto', cmap='RdBu_r', norm=norm)
    except Exception:
        im = ax3.imshow(hm, aspect='auto', cmap='RdBu_r', vmin=-vmax, vmax=vmax)
    ax3.set_yticks(range(len(hm_rows)), hm_rows, fontsize=6)
    ax3.set_xticks(range(len(annual_periods)), annual_periods)
    for i in range(hm.shape[0]):
        for j in range(hm.shape[1]):
            if np.isfinite(hm[i, j]):
                ax3.text(j, i, f'{hm[i, j]:+.1f}', ha='center', va='center', fontsize=5.5)
    ax3.set_title('All 33 matched factor deltas — annual net (%), grid standalone-year convention; '
                  'blue = challenger worse, red = challenger better', fontsize=9)
    fig.colorbar(im, ax=ax3, label='delta (pp, net)')
    fig.savefig(OUT / 'chart_factor_interactions.png', dpi=160, bbox_inches='tight')
    plt.close(fig)

    # ------------------------------------------- [7] 2022 attribution
    log('[7/10] 2022 attribution from standalone calendar-year runs ...')
    attr = {}
    attr_rows = []
    for lb, n, rb in ATTR_CONFIGS:
        key = cfg_name(lb, n, rb)
        geq, gdd, ghist, gstats = app.backtest(ranked[lb], f'Top {n}', rb,
                                               f'{ATTR_YEAR}-01-01', f'{ATTR_YEAR}-12-31', 0)
        net, ndd, hh, nstats = apply_costs(geq, ghist, gstats, COST_BPS)
        ref = pd.read_csv(GRID_DIR / f'{ATTR_YEAR}_{key}_net_nav.csv', index_col=0).iloc[:, 0]
        ref.index = pd.to_datetime(ref.index)
        ok = ref.index.equals(net.index) and bool(np.allclose(ref.values, net.values, rtol=0, atol=1e-9))
        recon(f'{ATTR_YEAR} {key} rerun == grid {ATTR_YEAR} standalone net curve', 'PASS' if ok else 'FAIL', '')
        attr[key] = dict(geq=geq, net=net, hist=ghist, gstats=gstats, nstats=nstats)

    # 2022 convention documentation block (discrepancy investigation, §9 of assignment)
    champ_key = cfg_name(CHAMP_LB, CHAMP_N, CHAMP_RB)
    cont_net_2022 = cont_year[champ_key]['net'].get(ATTR_YEAR)
    cont_gross_2022 = cont_year[champ_key]['gross'].get(ATTR_YEAR)
    conv_rows = [
        ('assignment / grid', 'grid standalone-year GROSS', float(grid_by_key[('2022', 10, 'Top10', 10)].gross_return)),
        ('grid artifact', 'grid standalone-year NET', float(grid_by_key[('2022', 10, 'Top10', 10)].net_return)),
        ('this study', 'continuous curve NET (2022-12-30 over 2021-12-31)', cont_net_2022),
        ('this study', 'continuous curve GROSS (2022-12-30 over 2021-12-31)', cont_gross_2022),
        ('adaptive study 20261010_073100 (reference, not recomputed)', 'continuous curve NET', -0.1820),
    ]
    log(f"      2022 champion conventions: standalone gross={pct(conv_rows[0][2])} standalone net={pct(conv_rows[1][2])} "
        f"continuous net={pct(cont_net_2022)} continuous gross={pct(cont_gross_2022)}")

    # monthly net tables
    monthly = {}
    for key, a in attr.items():
        mser = a['net'].groupby(a['net'].index.to_period('M')).last()
        mret = mser / mser.shift(1) - 1
        if len(mser):
            mret.iloc[0] = mser.iloc[0] / 1.0 - 1
        monthly[key] = mret
    months = sorted(set().union(*[set(v.index) for v in monthly.values()]))
    for key, mser in monthly.items():
        for m in mser.index:
            attr_rows.append(dict(component='monthly_net', config=key, detail=str(m),
                                  metric='net_return', value=float(mser.loc[m]), nature='retrospective_pnl_decomposition', notes=''))
    champ_monthly = monthly[champ_key]
    for key in attr:
        if key == champ_key:
            continue
        mret = monthly[key]
        for m in mret.index:
            if m in champ_monthly.index:
                attr_rows.append(dict(component='monthly_advantage_vs_champion', config=key, detail=str(m),
                                      metric='net_return_diff', value=float(mret.loc[m] - champ_monthly.loc[m]),
                                      nature='retrospective_pnl_decomposition', notes=''))

    # rank-band decomposition (equal-weight band returns per holding period)
    def band_analysis(key):
        a = attr[key]
        hist = a['hist']
        if hist.empty:
            return None
        entries = list(hist['entry_date'])
        exit_dates = entries[1:] + [str(a['net'].index.max().date())]
        band_rets = {b: [] for b in ('1-5', '6-10', '11-15', '16-20')}
        top10_rets, all_rets, disp = [], [], []
        for i, row in hist.iterrows():
            det = json.loads(row['target_details']) if isinstance(row['target_details'], str) else []
            e, x = entries[i], exit_dates[i]
            name_rets = {}
            for d in det:
                tk = d['ticker']
                if tk in px_raw.columns and pd.notna(px_raw.at[pd.Timestamp(e), tk]):
                    pv = px_val.at[pd.Timestamp(x), tk]
                    if pd.notna(pv) and px_raw.at[pd.Timestamp(e), tk] > 0:
                        name_rets[tk] = pv / px_raw.at[pd.Timestamp(e), tk] - 1
            if not name_rets:
                continue
            for d in det:
                tk = d['ticker']
                if tk not in name_rets:
                    continue
                rk = d['rank']
                for b in band_rets:
                    lo, hi = map(int, b.split('-'))
                    if lo <= rk <= hi:
                        band_rets[b].append(name_rets[tk])
            t10 = [name_rets[d['ticker']] for d in det if d['ticker'] in name_rets and d['rank'] <= 10]
            tall = list(name_rets.values())
            if t10:
                top10_rets.append(float(np.mean(t10)))
            all_rets.append(float(np.mean(tall)))
            if len(tall) > 1:
                disp.append(float(np.std(tall, ddof=1)))
        out = {}
        for b, vals in band_rets.items():
            out[b] = float(np.mean(vals)) if vals else np.nan
        out['top10'] = float(np.mean(top10_rets)) if top10_rets else np.nan
        out['all'] = float(np.mean(all_rets)) if all_rets else np.nan
        out['dispersion'] = float(np.mean(disp)) if disp else np.nan
        return out

    bands = {}
    for key in attr:
        b = band_analysis(key)
        bands[key] = b
        if b is None:
            continue
        for band, v in b.items():
            attr_rows.append(dict(component='band_return', config=key, detail=band,
                                  metric='avg_hold_period_return', value=v,
                                  nature='retrospective_pnl_decomposition',
                                  notes='equal-weight mean across holding periods'))
    b20 = bands.get(cfg_name(10, 20, 10))
    if b20 is not None:
        inc = float(np.nanmean([b20[b] for b in ('11-15', '16-20')]))
        attr_rows.append(dict(component='breadth_increment', config='MOM10/Top20/10D',
                              detail='rank 11-20 mean minus rank 1-10 mean', metric='avg_return_diff',
                              value=inc - b20['top10'], nature='retrospective_pnl_decomposition', notes=''))

    # midpoint reshuffle effect for the 5D config (MOM10/Top10/5D)
    sw_rows = []
    key5 = cfg_name(10, 10, 5)
    a5 = attr[key5]
    h5 = a5['hist']
    for i in range(len(h5)):
        if i % 2 == 1:
            m = pd.Timestamp(h5['entry_date'].iloc[i])
            nxt = pd.Timestamp(h5['entry_date'].iloc[i + 1]) if i + 1 < len(h5) else pd.Timestamp(str(a5['net'].index.max().date()))
            new_tk = h5['tickers'].iloc[i].split(',')
            old_tk = h5['tickers'].iloc[i - 1].split(',')
            def basket_ret(tks):
                rs = []
                for tk in tks:
                    if tk in px_val.columns:
                        p0 = px_val.at[m, tk]
                        p1 = px_val.at[nxt, tk]
                        if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                            rs.append(p1 / p0 - 1)
                return float(np.mean(rs)) if rs else np.nan
            rn, ro = basket_ret(new_tk), basket_ret(old_tk)
            if fin(rn) and fin(ro):
                diff = rn - ro
                sw_rows.append((m.strftime('%Y-%m-%d'), new_tk, old_tk, diff))
                attr_rows.append(dict(component='midpoint_swap', config=key5, detail=m.strftime('%Y-%m-%d'),
                                      metric='new_basket_minus_old_basket_5s_forward', value=diff,
                                      nature='retrospective_pnl_decomposition',
                                      notes=f"bought={len(set(new_tk)-set(old_tk))} sold={len(set(old_tk)-set(new_tk))}"))
    if sw_rows:
        diffs = np.array([r[3] for r in sw_rows])
        attr_rows.append(dict(component='midpoint_swap_summary', config=key5, detail=f'{len(sw_rows)} midpoint swaps',
                              metric='mean_diff', value=float(np.mean(diffs)),
                              nature='retrospective_pnl_decomposition',
                              notes=f'helped {int((diffs > 0).sum())} of {len(diffs)}'))
        attr_rows.append(dict(component='midpoint_swap_summary', config=key5, detail=f'{len(sw_rows)} midpoint swaps',
                              metric='sum_diff', value=float(np.sum(diffs)),
                              nature='retrospective_pnl_decomposition', notes=''))

    # MOM5 vs MOM10 leader overlap in 2022 (observable at signal date)
    ov_rows = []
    for sig in pd.to_datetime([r['signal_date'] for _, r in attr[champ_key]['hist'].iterrows()]):
        set5, _ = top_set(ranked[5], sig, 10)
        set10, _ = top_set(ranked[CHAMP_LB], sig, 10)
        ov = len(set5 & set10)
        ov_rows.append(dict(signal=pd.Timestamp(sig), overlap=ov, m5_only=sorted(set5 - set10), m10_only=sorted(set10 - set5)))
        attr_rows.append(dict(component='leader_overlap_m5_m10', config='Top10 set',
                              detail=pd.Timestamp(sig).strftime('%Y-%m-%d'),
                              metric='overlap_count_of_10', value=float(ov),
                              nature='observable_at_t', notes=''))
    fwd5, fwd10 = [], []
    for r in ov_rows:
        idx = cal.get_loc(r['signal'])
        if idx + 10 < len(cal):
            xf = cal[idx + 10]
            for tk in r['m5_only']:
                if tk in px_val.columns:
                    p0, p1 = px_val.at[r['signal'], tk], px_val.at[xf, tk]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        fwd5.append(p1 / p0 - 1)
            for tk in r['m10_only']:
                if tk in px_val.columns:
                    p0, p1 = px_val.at[r['signal'], tk], px_val.at[xf, tk]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        fwd10.append(p1 / p0 - 1)
    if ov_rows:
        attr_rows.append(dict(component='leader_overlap_summary', config='Top10 set',
                              detail=f'{len(ov_rows)} signal dates', metric='mean_overlap_of_10',
                              value=float(np.mean([r['overlap'] for r in ov_rows])),
                              nature='observable_at_t', notes=''))
    if fwd5:
        attr_rows.append(dict(component='leader_unique_forward', config='MOM5-only names',
                              detail=f'{len(fwd5)} name occurrences', metric='mean_10s_forward_return',
                              value=float(np.mean(fwd5)), nature='retrospective_outcome',
                              notes='names present in MOM5 Top10 but not MOM10 Top10'))
    if fwd10:
        attr_rows.append(dict(component='leader_unique_forward', config='MOM10-only names',
                              detail=f'{len(fwd10)} name occurrences', metric='mean_10s_forward_return',
                              value=float(np.mean(fwd10)), nature='retrospective_outcome',
                              notes='names present in MOM10 Top10 but not MOM5 Top10'))

    # top contributor concentration
    for key, a in attr.items():
        hist = a['hist']
        if hist.empty:
            continue
        entries = list(hist['entry_date'])
        exit_dates = entries[1:] + [str(a['net'].index.max().date())]
        contrib = {}
        for i, row in hist.iterrows():
            det = json.loads(row['target_details']) if isinstance(row['target_details'], str) else []
            e, x = entries[i], exit_dates[i]
            names = [d['ticker'] for d in det if d['ticker'] in px_raw.columns and pd.notna(px_raw.at[pd.Timestamp(e), d['ticker']])]
            if not names:
                continue
            for d in det:
                tk = d['ticker']
                if tk not in names:
                    continue
                pv = px_val.at[pd.Timestamp(x), tk]
                if pd.notna(pv):
                    r = pv / px_raw.at[pd.Timestamp(e), tk] - 1
                    contrib[tk] = contrib.get(tk, 0.0) + r / len(names)
        tot = sum(contrib.values())
        top3 = sorted(contrib.items(), key=lambda kv: -kv[1])[:3]
        for rk, (tk, v) in enumerate(top3, 1):
            attr_rows.append(dict(component='top_contributors', config=key, detail=f'#{rk} {tk}',
                                  metric='contribution_share', value=(v / tot if tot else np.nan),
                                  nature='retrospective_pnl_decomposition',
                                  notes=f'additive share of sum of holding-period returns; total add.={tot:.4f}'))
    pd.DataFrame(attr_rows).to_csv(OUT / f'{ATTR_YEAR}_attribution.csv', index=False, encoding='utf-8-sig')

    # 2022 chart
    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1], hspace=0.3, wspace=0.2)
    ax1 = fig.add_subplot(gs[0, :])
    colors = {champ_key: '#111111', cfg_name(10, 20, 10): '#37618e', cfg_name(10, 10, 5): '#27ae60',
              cfg_name(10, 20, 5): '#16a085', cfg_name(5, 10, 10): '#c0392b', cfg_name(5, 20, 10): '#e67e22'}
    for key, colr in colors.items():
        a = attr[key]
        ax1.plot(a['net'].index, a['net'].values, label=f"{key} ({a['nstats']['net_return']*100:+.2f}% net)", color=colr)
    ax1.set_title(f'{ATTR_YEAR} standalone net NAV — champion and challengers (production backtest, 5 bps)', fontsize=10)
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.25)
    ax2 = fig.add_subplot(gs[1, 0])
    if b20 is not None:
        labels = ['1-5', '6-10', '11-15', '16-20']
        vals = [b20[b] * 100 for b in labels]
        ax2.bar(labels, vals, color='#37618e')
        ax2.axhline(b20['top10'] * 100, color='#c0392b', ls='--', label=f"Top10 mean {b20['top10']*100:+.2f}%")
        for xi, v in enumerate(vals):
            ax2.text(xi, v, f'{v:+.2f}', ha='center', va='bottom' if v >= 0 else 'top', fontsize=8)
        ax2.set_title(f"{ATTR_YEAR} MOM10/Top20/10D: avg equal-weight return by rank band (hold periods)", fontsize=9)
        ax2.legend(fontsize=8)
        ax2.grid(alpha=0.25, axis='y')
    ax3 = fig.add_subplot(gs[1, 1])
    w = 0.21
    for j, key in enumerate([cfg_name(10, 10, 5), cfg_name(10, 20, 5), cfg_name(5, 10, 10), cfg_name(5, 20, 10)]):
        mret = monthly[key]
        adv = []
        for m in mret.index:
            adv.append((mret.loc[m] - champ_monthly.loc[m]) * 100 if m in champ_monthly.index else np.nan)
        xs = np.arange(len(mret.index))
        ax3.bar(xs + (j - 1.5) * w, adv, width=w, label=key)
    ax3.axhline(0, color='black', lw=1)
    ax3.set_xticks(np.arange(len(months)), [str(m)[-2:] for m in months], fontsize=8)
    ax3.set_xlabel('month of 2022')
    ax3.set_title(f'{ATTR_YEAR} monthly net advantage vs champion (pp)', fontsize=9)
    ax3.legend(fontsize=7)
    ax3.grid(alpha=0.25, axis='y')
    fig.savefig(OUT / f'chart_{ATTR_YEAR}_attribution.png', dpi=160, bbox_inches='tight')
    plt.close(fig)

    # ------------------------------------------- [8] reconciliation document
    log('[8/10] Writing reconciliation.md ...')
    sig10_list = [cal[i] for i in range(first_idx, len(cal) - 1, 10) if cal[i] <= last_v]
    sig10_2022 = [d for d in sig10_list if d.year == 2022][:4]
    recon_md = []
    recon_md.append('# Reconciliation — Strategy Factor Leading Indicators Study\n')
    recon_md.append(f'Generated {TS} · dataset `{UNIVERSE}` · fingerprint `{fingerprint}` · '
                    f'integrity status: {integrity_status} '
                    f'(completeness={completeness.get("status","n/a")}, counts={completeness.get("counts","n/a")})\n')
    recon_md.append('## Reference archives\n')
    recon_md.append(f'- Strategy Grid (18 configs): `{GRID_DIR.name}` — fingerprint `{grid_fp}` '
                    f'({"matches current" if grid_fp == fingerprint else "MISMATCH vs current"})')
    recon_md.append(f'- Champion archive: `{CHAMPION_DIR.name}` — fingerprint `{champ_fp}` '
                    f'({"matches current" if champ_fp == fingerprint else "MISMATCH vs current"})')
    recon_md.append('- Prior adaptive studies (reference evidence only, negative conclusions preserved, not re-run): '
                    '`results/studies/csi300_adaptive_rebalance/20261010_073100`, `results/studies/csi500_adaptive_rebalance/20261010_211520`')
    recon_md.append('- Older fixed-5D archive `ETF_250M_MOM10_20260918_204556_7e17aa4b` predates a dataset update '
                    '(fingerprint `2d2726...`); its numbers are cross-fingerprint and are not used as ground truth here.\n')
    recon_md.append('## Programmatic checks\n')
    for r in reconciliation:
        recon_md.append(f'- [{r["status"]}] {r["check"]}' + (f' — {r["detail"]}' if r["detail"] else ''))
    recon_md.append('\n## 2022 annual-return convention discrepancy (assignment §9)\n')
    recon_md.append('The assignment table (2022) and the adaptive study report different 2022 figures for Mom101010. '
                    'Both are correct under different, now-explicit conventions:\n')
    recon_md.append(md_table(['Source', 'Convention', '2022 champion return'],
                             [(a, b, pct(float(c))) for a, b, c in conv_rows]))
    recon_md.append('')
    recon_md.append(f'- Grid standalone-year runs hard-clip the window to the calendar year and re-anchor the 10-session '
                    f'phase at the first 2022 signal ({sig10_2022[0]:%Y-%m-%d}); the assignment table quotes GROSS returns '
                    f'(no cost), giving {pct(float(conv_rows[0][2]))}.')
    recon_md.append(f'- The same standalone run net of 5 bps one-way costs gives {pct(float(conv_rows[1][2]))} '
                    f'(cost drag {pct(float(conv_rows[1][2]) - float(conv_rows[0][2]))}).')
    recon_md.append(f'- The adaptive study and this study measure the year on the continuous curve: '
                    f'NAV({ATTR_YEAR}-12-30)/NAV(2021-12-31)-1, phase anchored at the 2021-01-29 signal, net → '
                    f'{pct(cont_net_2022)} (recorded reference: -18.20%), gross → {pct(cont_gross_2022)}.')
    recon_md.append('- Attribution of the difference: cost convention (gross vs net) explains the standalone pair '
                    f'({pct(float(conv_rows[0][2]))} vs {pct(float(conv_rows[1][2]))}); the remaining gap vs the continuous '
                    'figure reflects the rebalance-phase anchor (different trade dates through the year) and the '
                    'carried year-end 2021 basket, which are properties of a continuous run, not errors in either report. '
                    'No figure was changed; no attempt was made to harmonize them. Comparisons in this study are labeled '
                    'with their convention (grid_standalone_year vs continuous_curve) everywhere.\n')
    recon_md.append('## Forward-evaluation conventions (assignment §7)\n')
    recon_md.append('- Decision grid: champion signal dates cal[first_idx + 10k]; entry = next session (T+1); exit = '
                    'cal[p+11]; the 5D arm executes its midpoint trade at the t+5 signal entry (cal[p+6]).')
    recon_md.append('- Initial state: every arm for a window starts from the champion\'s carried portfolio '
                    '(shares + residual cash) valued immediately before the champion\'s scheduled trade at the entry '
                    'session, using production valuation (ffill for suspended names, buys at raw close only when '
                    'priced). The champion arm therefore reproduces the production champion window return exactly '
                    f'(max deviation {champ_dev:.2e}).')
    recon_md.append('- Net window return: gross window growth x product(1 - one_way_turnover x 5 bps) over trades '
                    'inside the window only; the next decision\'s entry cost is excluded symmetrically for all arms. '
                    'This is a window-scoped convention and equals the production net curve only between consecutive '
                    'decision entries.')
    recon_md.append('- No equity-curve splicing: each window is a standalone paired counterfactual; windows are '
                    'diagnostics, not a deployable strategy. No adaptive portfolio rule was constructed (out of scope '
                    'per assignment §7/§11).\n')
    recon_md.append('## Hypothesis classification aids\n')
    recon_md.append('Descriptive rules (documented before interpretation, not optimized): a pair is flagged '
                    '"Supported for further prospective validation (in-sample only)" only if the block-bootstrap 95% CI '
                    'of the mean forward advantage excludes zero AND annual means match the full-period sign in >=5/6 '
                    'years AND the tercile spread is at least 50 bps per window with the sign of the rank '
                    'correlation; "Not supported" if none of the three holds; otherwise "Inconclusive".')
    (OUT / 'reconciliation.md').write_text('\n'.join(recon_md), encoding='utf-8')

    # ------------------------------------------- [9] report
    log('[9/10] Writing report.md ...')
    def fmt_delta(basis, period, label):
        dn, dg = D[(basis, period, label)]
        return (num(dn * 100, 2), num(dg * 100, 2))

    t_deltas = []
    for label, _ in primary_labels:
        row = [label]
        for pp in annual_periods:
            row.append(fmt_delta('grid_standalone_year', pp, label)[0])
        row.append(fmt_delta('grid_standalone_year', 'Full Period', label)[0])
        row.append(fmt_delta('grid_standalone_year', 'Full Period', label)[1])
        t_deltas.append(row)
    inter_fp = inter[(inter['basis'] == 'grid_standalone_year') & (inter['period'] == 'Full Period') &
                     (inter['axis'].str.startswith('interaction'))]
    inter_by = {(ib.basis, ib.period, ib.comparison): (ib.delta_net, ib.delta_gross) for ib in inter.itertuples()}
    inter_rows_md = []
    for r in inter_fp.itertuples():
        years = [inter_by[('grid_standalone_year', pp, r.comparison)][0] for pp in annual_periods]
        pos = sum(1 for y in years if np.isfinite(y) and np.sign(y) == np.sign(r.delta_net))
        inter_rows_md.append([r.comparison, num(r.delta_net * 100, 2), num(r.delta_gross * 100, 2), f'{pos}/6'])

    hyp_lines = []
    for hyp, hname in (('A', 'VOL20 → Top20 vs Top10 advantage (MOM10/10D)'),
                       ('B', 'Leadership instability → 5D vs 10D advantage (MOM10/Top10)'),
                       ('C', 'RS20 → MOM5 vs MOM10 advantage (Top10/10D)')):
        c = classifications[hyp]
        hyp_lines.append(f'- **Hypothesis {hyp}** ({hname}): **{c["status"]}** — {c["detail"]}')

    strong_bits = []
    for ind_name, comp, _ in prim:
        row = diag[(diag['indicator'] == ind_name) & (diag['comparison'] == comp)].iloc[0]
        strong_bits.append(f"- {ind_name} vs {comp}: n={int(row['n_events'])}, mean net advantage {num(row['mean_adv_net']*100,2)}pp "
                           f"per 10 sessions, win rate {num(row['win_rate_net'],2)}, spearman {num(row['spearman'],3)}, "
                           f"terciles low/mid/high = {num(row['tercile_low_mean']*100,2)}/{num(row['tercile_mid_mean']*100,2)}/"
                           f"{num(row['tercile_high_mean']*100,2)} pp, 95% CI mean = [{num(row['boot_mean_lo']*100,2)}, {num(row['boot_mean_hi']*100,2)}] pp.")

    report = []
    report.append('# Strategy Factor Leading Indicators — Study Report\n')
    report.append(f'Generated {TS} · `results/studies/strategy_factor_leading_indicators/{TS}` · '
                  f'**EXPLORATORY, in-sample, research-only**. Dataset fingerprint `{fingerprint}`. '
                  'No production logic, defaults, archives, or the production champion were modified.\n')
    report.append('## 1. Executive summary\n')
    report.append('- Question: can observable conditions at decision date t (CSI 500 trailing realized volatility, '
                  'point-in-time Top-10 leadership turnover, CSI 500 vs CSI 300 relative strength) predict the forward '
                  'advantage of changing momentum lookback (M), breadth (N), or rebalance interval (H) away from the '
                  'champion MOM10/Top10/10D?')
    report.append(f'- Evaluation: {len(fa)} paired, non-spliced 10-session windows on the champion decision grid, '
                  f'{cal[decisions[0]]:%Y-%m-%d} .. {cal[decisions[-1]]:%Y-%m-%d}, all arms re-anchored to the '
                  'champion\'s documented initial portfolio state; net of 5 bps one-way costs inside each window.')
    report.extend(hyp_lines)
    report.append('- Factor interactions (grid standalone-year convention, net): see section 2 — the dominant effect is '
                  'the rebalance-interval contrast; the champion\'s factor combination was not displaced by any '
                  'single-factor change on full-period net returns while challengers did beat it in 2022.')
    report.append('- No adaptive rule is proposed or evaluated; nothing here changes the production champion or '
                  'justifies one. Negative findings are reported as such.\n')
    report.append('## 2. Factor interactions (assignment §5)\n')
    report.append('Source: the validated 18-config Strategy Grid run (standalone calendar years, hard-clipped) and the '
                  f'continuous curves recomputed here from the same fingerprint. Net returns; gross in `factor_interactions.csv`.\n')
    report.append(md_table(['comparison'] + annual_periods + ['Full net', 'Full gross'], t_deltas))
    report.append('\nInteraction terms (full period, net & gross pp; sign-consistency = years whose annual net delta has '
                  'the same sign as the full period):\n')
    report.append(md_table(['interaction', 'Full net (pp)', 'Full gross (pp)', 'years same sign'], inter_rows_md))
    report.append('\n' + '![factor interactions](chart_factor_interactions.png)\n')
    report.append('Reading: the 5D−10D contrast is the largest and most consistent single-factor effect; breadth '
                  '(Top20−Top10) is mixed by year; MOM5/MOM20 vs MOM10 is sign-inconsistent. Interaction terms are '
                  'reported for completeness; none is used to propose a rule.\n')
    report.append('## 3. 2022 attribution (assignment §6)\n')
    t22 = []
    for key, a in attr.items():
        t22.append([key, pct(a['gstats']['total_return']), pct(a['nstats']['net_return']),
                    pct(cont_year[key]['net'].get('2022'))])
    report.append(md_table(['config', '2022 standalone gross', '2022 standalone net', '2022 continuous net'], t22))
    report.append('')
    champ_m = [m for m in champ_monthly.index if champ_monthly.loc[m] < 0]
    report.append(f'- Same periods / avoided exposures: champion was negative in {len(champ_m)} of '
                  f'{len(champ_monthly)} months; monthly net correlation vs challengers in `2022_attribution.csv` '
                  '(component monthly_advantage_vs_champion).')
    if b20 is not None:
        report.append(f"- Ranks 11–20: MOM10/Top20/10D average equal-weight band returns were 1–5 {pct(b20['1-5'])}, "
                      f"6–10 {pct(b20['6-10'])}, 11–15 {pct(b20['11-15'])}, 16–20 {pct(b20['16-20'])} per holding period; "
                      f"Top10 mean {pct(b20['top10'])} vs all-20 mean {pct(b20['all'])}; avg cross-sectional dispersion "
                      f"{num(b20['dispersion'],4)}.")
    if sw_rows:
        diffs = np.array([r[3] for r in sw_rows])
        report.append(f"- 5D midpoint reshuffles (MOM10/Top10/5D): {len(sw_rows)} swaps inside 2022, "
                      f"mean new-minus-old basket 5-session return {num(np.mean(diffs)*100,2)}pp, helped "
                      f"{int((diffs>0).sum())}/{len(diffs)}.")
    if ov_rows:
        report.append(f"- MOM5 vs MOM10 leadership: mean Top-10 overlap {num(np.mean([r['overlap'] for r in ov_rows]),1)}/10 "
                      f"across {len(ov_rows)} 2022 signal dates; unique-name forward returns in `2022_attribution.csv` "
                      '(leader_unique_forward).')
    report.append('- Observable vs retrospective: holdings composition, rank bands present at t, leader overlap and the '
                  'midpoint decision schedule are observable at decision time; the band/swap/months return decompositions '
                  'are retrospective accounting of what happened afterwards and are labeled as such in the CSV. '
                  'They are candidate explanations, not leading indicators.\n')
    report.append(f'![2022 attribution](chart_{ATTR_YEAR}_attribution.png)\n')
    report.append('## 4. Indicator results (assignment §7/§8)\n')
    report.append('Primary pairs (block bootstrap, moving blocks of 3 decisions, 2000 resamples, seed 20261010):\n')
    report.extend(strong_bits)
    report.append('')
    report.append(md_table(['indicator', 'comp', 'role', 'n', 'mean net (pp)', 'median (pp)', 'win rate', 'spearman',
                            'tercile hi-lo (pp)', 'Drop-extreme mean (pp)', '2022 mean (pp)', 'ex-2022 mean (pp)', 'cost effect (pp)'],
                           [[r.indicator, r.comparison, r.role, int(r.n_events), num(r.mean_adv_net*100,2),
                             num(r.median_adv_net*100,2), num(r.win_rate_net,2), num(r.spearman,3),
                             num(r.tercile_high_minus_low*100,2), num(r.mean_drop_extreme*100,2),
                             num(r.mean_2022*100,2), num(r.mean_ex2022*100,2), num(r.mean_cost_effect*100,2)]
                            for r in diag.itertuples() if r.role in ('primary', 'secondary')]))
    report.append('')
    report.append(f'![indicator vs forward advantage](chart_indicator_forward_advantage.png)\n')
    report.append('- Statistics caveats (assignment §8): all results are in-sample and post-hoc (the hypotheses were '
                  'formed after observing 2022); overlapping-window dependence is handled only approximately by moving-block '
                  'bootstrap CIs; 16 indicator×comparison pairs are computed (3 primary, 2 secondary, 11 cross-checks) so '
                  'multiple-testing inflation applies — treat cross-check pairs as exploratory. Nothing is labeled '
                  'out-of-sample; no threshold was optimized; terciles are descriptive.')
    report.append('- Economic significance: the primary pairs\' mean advantages per 10-session window are small relative '
                  'to noise and must be judged against one extra rebalance cost (~2–4 bps one-way turnover x 5 bps); '
                  'the cost-effect column shows the gross-to-net shift per window.\n')
    report.append('## 5. Hypothesis assessment (assignment §11)\n')
    report.extend(hyp_lines)
    report.append('- An adaptive portfolio was deliberately NOT built: per the assignment, historical fit alone must not '
                  'be converted into production rules, and prior adaptive experiments (CSI300/CSI500) already returned '
                  'negative results; those conclusions are preserved and reference-only.\n')
    report.append('## 6. Data & reconciliation status (assignment §9)\n')
    report.append(f'- Dataset `{UNIVERSE}`, fingerprint `{fingerprint}`, integrity **{integrity_status}** '
                  f'(completeness {completeness.get("status","n/a")}: {completeness.get("counts","n/a")} — recorded as-is; '
                  'research was allowed by the validators under existing project policy).')
    report.append('- All 18 continuous configs reproduce the fingerprint-matched grid full-period net curves; the champion '
                  'reproduces its archive; the stateful simulator reproduces `backtest()` exactly; the champion paired arm '
                  f'reproduces the champion curve ratio on every window (max deviation {champ_dev:.2e}).')
    report.append(f'- 2022 convention discrepancy (grid gross {pct(float(conv_rows[0][2]))} vs continuous net '
                  f'{pct(cont_net_2022)}, adaptive study -18.20%): investigated, documented, and NOT reconciled — see '
                  '`reconciliation.md`. Each figure is correct under its stated convention; all comparisons here carry '
                  'explicit convention labels.')
    report.append('- Cross-fingerprint references (older 5D archive, prior studies) are flagged in `reconciliation.md` and '
                  'were not used as ground truth.\n')
    report.append('## 7. Artifacts\n')
    report.append('| file | contents |\n| --- | --- |\n'
                  '| report.md | this report |\n'
                  '| factor_interactions.csv | all matched factor deltas + interaction terms, both conventions, net & gross |\n'
                  '| config_period_returns.csv | wide 18 config x period return matrix (both conventions) |\n'
                  '| indicator_events.csv | long indicator x comparison x decision events (16 pairs) |\n'
                  '| forward_advantages.csv | wide per-decision paired arm returns and advantages |\n'
                  '| predictive_diagnostics.csv | per-pair statistics, terciles, bootstrap CIs, annual/2022 splits, sensitivity |\n'
                  f'| {ATTR_YEAR}_attribution.csv | 2022 attribution components (monthly, bands, swaps, overlap, contributors, conventions) |\n'
                  '| reconciliation.md | all checks + 2022 convention investigation |\n'
                  '| evidence_index.md | conclusion-to-artifact map: which files/fields carry each claim, with inputs |\n'
                  '| report_data.json | machine-readable summary |\n'
                  '| chart_factor_interactions.png, chart_indicator_forward_advantage.png, '
                  f'chart_{ATTR_YEAR}_attribution.png | charts |\n')
    report.append('## 8. Files created / modified & production status\n')
    report.append(f'- Created: `studies/strategy_factor_leading_indicators_study.py` and everything under `{ART_DIR_REL}`.')
    report.append('- Modified: none. Reused unmodified: `backtest()`, `apply_costs`, `select_target_rows`, `compare_weights`, '
                  'signal/ranking pipeline, benchmark loader.')
    report.append('- Explicit confirmation: **no production strategy logic, configuration, defaults, datasets, archives, '
                  'UI, scheduling, or ingestion code was changed; Mom101010 remains the unchanged production champion; '
                  'no commits or pushes were made.**\n')
    (OUT / 'report.md').write_text('\n'.join(report), encoding='utf-8')

    # ------------------------------------------- evidence index
    log('[9/10] Writing evidence_index.md ...')
    n_pass = sum(1 for r_ in reconciliation if r_['status'] == 'PASS')
    n_nonpass = len(reconciliation) - n_pass
    prim_ev = []
    for ind_name, comp, _ in prim:
        r0 = diag[(diag['indicator'] == ind_name) & (diag['comparison'] == comp)].iloc[0]
        prim_ev.append((ind_name, comp, int(r0['n_events'])))
    evi = []
    evi.append('# Evidence index — which artifacts support which conclusions\n')
    evi.append(f'Generated {TS} · dataset `{UNIVERSE}` fingerprint `{fingerprint}`. Every file in this directory '
               'was written by `studies/strategy_factor_leading_indicators_study.py` (sha256 recorded in '
               'report_data.json) in one deterministic run; no artifact was hand-edited. This index maps each '
               'conclusion in report.md to the files and fields that carry its evidence.\n')
    evi.append('## Study inputs (read-only; referenced, not modified, not copied)\n')
    evi.append(md_table(['input', 'location', 'role'], [
        ['dataset', f'`{UNIVERSE}` ranked MOM5/MOM10/MOM20 panels + membership + prices; fingerprint '
                    f'`{fingerprint}`; integrity {integrity_status} '
                    f'(completeness {completeness.get("status","n/a")} {completeness.get("counts","n/a")}, recorded as-is)',
         'signals, prices, membership for every arm and every indicator'],
        ['benchmark closes', 'CSI 500 `H00905.CSI` and CSI 300 `H00300.CSI` total-return series (benchmark store)',
         'VOL20 (CSI500 trailing 20-session realized vol) and RS20 (CSI500 minus CSI300 20-session return)'],
        ['Strategy Grid run', f'`results/backtests/{GRID_DIR.name}` — fingerprint `{grid_fp}`',
         'section 2 factor deltas and standalone calendar-year reference curves (18 configs x 7 periods)'],
        ['champion archive', f'`results/backtests/{CHAMPION_DIR.name}` — fingerprint `{champ_fp}`',
         'champion net curve/stats reconciliation; carried portfolio state anchoring every window'],
        ['prior adaptive studies', '`results/studies/csi300_adaptive_rebalance/20261010_073100`, '
                                   '`results/studies/csi500_adaptive_rebalance/20261010_211520` (not recomputed)',
         'reference evidence only; negative conclusions preserved verbatim'],
    ]))
    evi.append('## Conclusion to evidence map\n')
    evi.append(md_table(['conclusion (report section)', 'artifact(s)', 'fields / rows to read'], [
        ['factor interaction deltas and interaction terms (section 2)',
         '`factor_interactions.csv`; `chart_factor_interactions.png`; `config_period_returns.csv`',
         f'all {len(inter)} rows = ({len(pairs_spec)} pairwise + {len(inter_specs)} interaction) comparisons x 2 '
         'conventions x 7 periods; columns delta_net/delta_gross per basis and period; the wide matrix holds each '
         'config\'s return per period under both bases'],
        ['2022 attribution components (section 3)',
         '`2022_attribution.csv`; `chart_2022_attribution.png`',
         f'{len(attr_rows)} rows across components monthly_net, monthly_advantage_vs_champion, band_return, '
         'breadth_increment, midpoint_swap(+summary), leader_overlap_m5_m10(+summary), leader_unique_forward, '
         'top_contributors, and the 2022 convention rows; column nature separates observable vs '
         'retrospective_pnl_decomposition'],
        ['primary indicator statistics VOL20/Instability5/RS20 (section 4)',
         '`predictive_diagnostics.csv`; `indicator_events.csv`; `forward_advantages.csv`; '
         '`chart_indicator_forward_advantage.png`',
         'diagnostics rows with role=primary (n_events, mean/median_adv_net, win_rate_net, pearson, spearman, '
         'boot_mean_lo/hi, boot_spearman_lo/hi, tercile_* means and hit rates, boot_spread_lo/hi, annual_means, '
         'mean_2022, mean_ex2022, mean_drop_extreme, mean_cost_effect); events = one row per decision per pair '
         '(indicator_value, adv_net, adv_gross)'],
        ['hypothesis A/B/C classification (sections 1, 5)',
         '`predictive_diagnostics.csv` (primary rows); classification rule in `reconciliation.md` '
         '(section "Hypothesis classification aids"); `report_data.json` field classifications',
         'CI excludes zero = boot_mean_lo/hi; year sign consistency = annual_means vs mean_adv_net; tercile spread '
         '= tercile_high_minus_low vs 50 bps and sign vs spearman'],
        ['paired window construction and arm returns (sections 3, 4; assignment section 7)',
         '`forward_advantages.csv`',
         f'{len(fa)} windows: s0_value (shared initial state), champ/a/b/c/c2 gross and net returns, adv_* columns '
         '= arm minus comparison arm, plus vol20/instability5/instability10/rs20 at the decision date'],
        ['2022 annual-return convention discrepancy (section 6)',
         '`reconciliation.md` (convention table); `config_period_returns.csv`; `report_data.json` field convention_2022',
         '2022_net / 2022_gross columns under both bases for MOM10_Top_10_10D; standalone vs continuous rows'],
        ['data and reconciliation status (section 6)',
         '`reconciliation.md`; `report_data.json` fields reconciliation, references, dataset, conventions',
         f'{n_pass} PASS / {n_nonpass} non-PASS checks; fingerprints of dataset, grid run and champion archive; '
         'single observed failure mode would be reported not hidden'],
    ]))
    evi.append('## Reproduction\n')
    evi.append(f'- Single command: `python studies/strategy_factor_leading_indicators_study.py` '
               f'(run time ~10-12 min; deterministic: the hypothesis numbers were bit-identical across the three '
               f'executions that reached the classification stage, of which this is the one that completed all '
               f'artifacts).')
    evi.append(f'- Decision grid: {len(fa)} champion decision windows, '
               f'{cal[decisions[0]]:%Y-%m-%d} .. {cal[decisions[-1]]:%Y-%m-%d}; '
               f'{len(ev)} indicator-event rows ({len(indicators)} indicators x {len(comparisons)} comparisons x '
               f'{len(fa)} windows; primary pairs have {prim_ev[0][2]} finite events after warm-up).')
    evi.append('- The script reads the dataset, grid run, champion archive and benchmark store; it writes only inside '
               'this timestamped directory.\n')
    (OUT / 'evidence_index.md').write_text('\n'.join(evi), encoding='utf-8')

    # ------------------------------------------- [10] report_data.json
    log('[10/10] Writing report_data.json ...')
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rd = dict(
        study='strategy_factor_leading_indicators', generated=TS, script='studies/strategy_factor_leading_indicators_study.py',
        script_sha256=script_sha, artifact_dir=ART_DIR_REL,
        dataset=dict(universe=UNIVERSE, fingerprint=fingerprint, integrity_status=integrity_status,
                     completeness=completeness.get('status'), completeness_counts=completeness.get('counts')),
        references=dict(grid_dir=GRID_DIR.name, grid_fingerprint=grid_fp, champion_dir=CHAMPION_DIR.name,
                        champion_fingerprint=champ_fp),
        conventions=dict(cost_bps=COST_BPS, window_sessions=WINDOW_SESSIONS, decision_dates=int(len(fa)),
                         first_decision=str(cal[decisions[0]].date()), last_decision=str(cal[decisions[-1]].date()),
                         initial_state='champion carried portfolio before scheduled entry trade',
                         net_scope='window trades only (next-entry cost excluded symmetrically)',
                         champion_arm_max_deviation=champ_dev),
        reconciliation=reconciliation,
        classifications=classifications,
        convention_2022=[dict(source=a, convention=b, value=c) for a, b, c in conv_rows],
        primary_pairs={f'{a}_{b}': dict(n=int(r.n_events), mean_adv_net=r.mean_adv_net, win=r.win_rate_net,
                                        spearman=r.spearman, tercile_hi_lo=r.tercile_high_minus_low)
                       for r in diag.itertuples() if r.role == 'primary'
                       for a, b in [(r.indicator, r.comparison)]},
        full_period_summary={k: dict(net_return=v['nstats']['net_return'], net_cagr=v['nstats']['net_cagr'],
                                     net_max_drawdown=v['nstats']['net_max_drawdown'],
                                     gross_return=v['gstats']['total_return'], rebalances=v['gstats']['rebalances'])
                             for k, v in curves.items()},
        artifacts=sorted(p.name for p in OUT.iterdir()),
        production_modified=False,
    )
    (OUT / 'report_data.json').write_text(json.dumps(clean(rd), indent=1, ensure_ascii=False), encoding='utf-8')

    log(f"DONE. Artifacts in {OUT}")
    log(f"Hypothesis A: {classifications['A']['status']}")
    log(f"Hypothesis B: {classifications['B']['status']}")
    log(f"Hypothesis C: {classifications['C']['status']}")


if __name__ == '__main__':
    main()

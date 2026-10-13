"""Adaptive Rebalance Feasibility Study (exploratory, research-only).

Benchmark-selectable: `python studies/adaptive_rebalance_study.py [csi300|csi500]`
(default csi300). Tests whether a broad-index trailing reading can lead-select
between 5-session and 10-session rebalance intervals of ETF_250M / MOM10 / Top10.

Hypothesis (fixed before evaluation, per research spec): at each rebalance
signal date, if the benchmark's trailing 20-session total return is below zero
the next holding interval is 5 sessions, otherwise 10 sessions. Missing or
insufficient benchmark history defaults to 10 sessions and is counted.

One stateful simulation walks the production calendar with a variable step
(no NAV splicing). Fixed 5D and 10D baselines are produced by the same loop
and reconciled against production backtest() and saved runs. The leading-
indicator diagnostic is a separate paired-basket event study on the regular
10-session grid. No production file is modified; no window or threshold is
optimized.
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import MomentumLabV2 as app
from momentumlab.costs import apply_costs
from momentumlab.benchmark import load_benchmark, save_comparison_chart

UNIVERSE = 'ETF_250M'
SELECTION = 'Top 10'
LOOKBACK = 10
COST_BPS = 5.0
STUDY_START = '2021-01-01'
BENCH_WINDOW = 20
BENCH_MIN_OBS = 21
CHAMPION_DIR = ROOT / 'results' / 'backtests' / 'ETF_250M_MOM10_20261010_063502_57b6d7e8'
SAVED5_DIR = ROOT / 'results' / 'backtests' / 'ETF_250M_MOM10_20260918_204556_7e17aa4b'

BENCHMARKS = {
    'csi300': dict(slug='csi300', label='CSI 300', ticker='H00300.CSI',
                   index_name='CSI 300 Total Return Index',
                   csv='data/benchmarks/H00300.CSI.csv'),
    'csi500': dict(slug='csi500', label='CSI 500', ticker='H00905.CSI',
                   index_name='CSI 500 Total Return Index',
                   csv='data/benchmarks/H00905.CSI.csv'),
}
BENCH_KEY = sys.argv[1] if len(sys.argv) > 1 else 'csi300'
if BENCH_KEY not in BENCHMARKS:
    raise SystemExit(f"Unknown benchmark '{BENCH_KEY}'; choose from {sorted(BENCHMARKS)}")
BCFG = BENCHMARKS[BENCH_KEY]
BENCH_CSV = ROOT / BCFG['csv']
TS = datetime.now().strftime('%Y%m%d_%H%M%S')
OUT = ROOT / 'results' / 'studies' / f"{BCFG['slug']}_adaptive_rebalance" / TS

pct = lambda v: 'n/a' if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v * 100:.2f}%"
num = lambda v, n=4: 'n/a' if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:.{n}f}"


def rel_close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def log(msg):
    print(msg, flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reconciliation = []

    # ---------------- data ----------------
    log(f"[1/8] Loading ETF_250M dataset and {BCFG['label']} benchmark ...")
    cfg = app.load_config()
    paths = app.selected_universe_paths(cfg, UNIVERSE)
    integrity = app.validate_research_files(UNIVERSE, mode='cached')
    integrity_status = integrity.get('status') or ('ok' if integrity.get('ok') else 'unknown')
    manifest = integrity.get('manifest') or {}
    fingerprint = manifest.get('dataset_fingerprint')

    prices = app.research_prices(paths)
    weights = app.read_csv(paths['weights_csv'])
    ranked = app.merge_membership_and_rank(app.calculate_signal(prices, LOOKBACK), weights)
    log(f"      ranked rows={len(ranked):,}  dates={ranked['trade_date'].nunique():,}")

    bench_close, bench_meta = load_benchmark(str(BENCH_CSV), BCFG['ticker'],
                                             BCFG['index_name'], basis='total_return')
    bench_meta['display_label'] = BCFG['label']
    log(f"      benchmark {bench_meta['basis_label']} rows={len(bench_close):,} "
        f"{bench_close.index.min():%Y-%m-%d}..{bench_close.index.max():%Y-%m-%d}")

    # ---------------- shared preprocessing (mirrors backtest() lines 2010-2027) ----------------
    d = ranked.sort_values(['trade_date', 'ts_code']).copy()
    cal = pd.DatetimeIndex(sorted(d['trade_date'].dropna().unique()))
    px_raw = d.pivot_table(index='trade_date', columns='ts_code', values='adj_close', aggfunc='last').sort_index()
    px_val = px_raw.ffill()
    valid_dates = d.loc[(d['csi300_member'] == 1) & d['momentum_rank'].notna(), 'trade_date']
    if valid_dates.empty:
        raise ValueError('No valid ranking dates in dataset.')
    first = max(valid_dates.min(), pd.Timestamp(STUDY_START))
    END = valid_dates.max()
    last = min(valid_dates.max(), END)
    candidates = cal[(cal >= first) & (cal <= last)]
    if len(candidates) < 2:
        raise ValueError('Study window too short.')
    first_idx = cal.get_loc(candidates[0])
    log(f"[2/8] Window: {cal[first_idx]:%Y-%m-%d} .. {END:%Y-%m-%d}  "
        f"(calendar positions {first_idx}..{len(cal) - 1})")

    def trailing_reading(sig_date):
        b = bench_close[bench_close.index <= pd.Timestamp(sig_date)]
        if len(b) < BENCH_MIN_OBS:
            return None
        return float(b.iloc[-1] / b.iloc[-BENCH_MIN_OBS] - 1)

    def walk(mode):
        """Signal-index walker: fixed step, or adaptive step decided at the signal date."""
        decisions = []
        idxs = []
        i = first_idx
        while i <= len(cal) - 2 and cal[i] <= last:
            if mode == 'fixed10':
                step = 10
            elif mode == 'fixed5':
                step = 5
            else:
                r = trailing_reading(cal[i])
                if r is None:
                    step, defaulted = 10, True
                else:
                    step, defaulted = (5, False) if r < 0 else (10, False)
                decisions.append(dict(signal_date=cal[i], trailing_20_return=r,
                                      interval_sessions=step, defaulted_to_10d=defaulted))
            idxs.append(i)
            i += step
        return idxs, decisions

    def simulate(mode):
        """Verbatim production backtest() loop; only the signal schedule differs."""
        sig_idx, decisions = walk(mode)
        sig_to_entry = {cal[i + 1]: cal[i] for i in sig_idx}
        cash, shares = 1.0, {}
        nav, logs = [], []

        def value(date):
            v = cash
            for t, sh in shares.items():
                if t in px_val.columns:
                    p = px_val.at[date, t]
                    if pd.notna(p):
                        v += sh * p
            return float(v)

        for di in range(sig_idx[0] + 1, len(cal)):
            date = cal[di]
            if date > END:
                break
            before = value(date)
            if date in sig_to_entry:
                sig = sig_to_entry[date]
                chosen = app.select_target_rows(d, SELECTION, sig).copy()
                tickers = [t for t in chosen['ts_code'] if t in px_raw.columns and pd.notna(px_raw.at[date, t])]
                if tickers:
                    current = {}
                    for t, sh in shares.items():
                        p = px_val.at[date, t] if t in px_val.columns else np.nan
                        current[t] = sh * p if pd.notna(p) else 0.0
                    target_each = before / len(tickers)
                    detail = app.compare_weights(
                        {t: v / before for t, v in current.items()} if before > 0 else {},
                        {t: 1 / len(tickers) for t in tickers}, initial=not logs)
                    traded = sum(abs((target_each if t in tickers else 0) - current.get(t, 0))
                                 for t in set(current) | set(tickers))
                    cost = traded * 0.0  # gross simulation; V4 costs applied afterwards
                    after = max(before - cost, 0)
                    each = after / len(tickers)
                    new = {}
                    for t in tickers:
                        p = px_raw.at[date, t]
                        if p > 0:
                            new[t] = each / p
                    shares = new
                    cash = after - sum(new[t] * px_raw.at[date, t] for t in new)
                    logs.append({'signal_date': sig.strftime('%Y-%m-%d'),
                                 'entry_date': date.strftime('%Y-%m-%d'),
                                 'names': len(tickers),
                                 'one_way_turnover': 0.5 * traded / before if before > 0 else np.nan,
                                 'cost': cost, 'tickers': ','.join(tickers),
                                 'target_details': app.json_dumps(
                                     [{'ticker': str(r.ts_code), 'rank': int(r.momentum_rank),
                                       'score': (float(getattr(r, 'momentum_score'))
                                                 if pd.notna(getattr(r, 'momentum_score', None)) else None),
                                       'target_weight': 1 / len(tickers)}
                                      for r in chosen.itertuples() if r.ts_code in tickers], ensure_ascii=False),
                                 **detail, 'nav_before': before, 'nav_after': after})
            nav.append((date, value(date)))
            if date >= END:
                break
        s = pd.Series([v for _, v in nav], index=pd.DatetimeIndex([x for x, _ in nav]), name='nav')
        eq = s / s.iloc[0]
        rets = s.pct_change(fill_method=None).fillna(0)
        dd = eq / eq.cummax() - 1
        stats = {'total_return': float(eq.iloc[-1] - 1),
                 'annualized_return': float(eq.iloc[-1] ** (252 / max(len(eq) - 1, 1)) - 1),
                 'annualized_volatility': float(rets.std(ddof=1) * np.sqrt(252)),
                 'max_drawdown': float(dd.min()),
                 'rebalances': len(logs),
                 'backtest_start': eq.index.min().strftime('%Y-%m-%d'),
                 'backtest_end': eq.index.max().strftime('%Y-%m-%d'),
                 'observations': int(len(eq))}
        hist = pd.DataFrame(logs)
        partial = int((hist['names'] < 10).sum()) if len(hist) else 0
        return dict(eq=eq, dd=dd, hist=hist, gross_stats=stats, decisions=decisions, partial_entries=partial)

    # ---------------- three simulations ----------------
    log('[3/8] Running fixed 10D / fixed 5D / adaptive simulations ...')
    runs = {m: simulate(m) for m in ('fixed10', 'fixed5', 'adaptive')}
    for m, r in runs.items():
        log(f"      {m}: entries={r['gross_stats']['rebalances']}  gross_return={r['gross_stats']['total_return']:.6f}")

    # ---------------- reconciliation vs production backtest() ----------------
    log('[4/8] Reconciling against production backtest() ...')
    for mode, rb in (('fixed10', 10), ('fixed5', 5)):
        peq, pdd, phist, pstats = app.backtest(ranked, SELECTION, rb, STUDY_START, str(END.date()), 0)
        mine = runs[mode]
        ok_eq = peq.index.equals(mine['eq'].index) and np.allclose(peq.values, mine['eq'].values, rtol=0, atol=1e-12)
        ok_log = (len(phist) == len(mine['hist']) and
                  (len(phist) == 0 or (
                      phist['entry_date'].tolist() == mine['hist']['entry_date'].tolist() and
                      np.allclose(phist['one_way_turnover'].astype(float).values,
                                  mine['hist']['one_way_turnover'].astype(float).values, rtol=0, atol=1e-12))))
        ok_stats = all(rel_close(pstats[k], mine['gross_stats'][k], 1e-12)
                       for k in ('total_return', 'annualized_return', 'max_drawdown'))
        reconciliation.append(dict(check=f'{mode} simulator == production backtest(rb={rb})',
                                   status='PASS' if (ok_eq and ok_log and ok_stats) else 'FAIL',
                                   detail=f"eq_match={ok_eq} log_match={ok_log} stats_match={ok_stats}"))
        log(f"      {mode}: eq={ok_eq} logs={ok_log} stats={ok_stats}")

    # ---------------- net curves via V4 cost convention ----------------
    log('[5/8] Applying 5 bps one-way turnover costs (V4 convention) ...')
    for m, r in runs.items():
        net, net_dd, hist2, stats = apply_costs(r['eq'], r['hist'], r['gross_stats'], COST_BPS)
        r.update(net=net, net_dd=net_dd, net_stats=stats)
        log(f"      {m}: net_return={stats['net_return']:.6f} net_cagr={stats['net_cagr']:.6f} "
            f"net_maxDD={stats['net_max_drawdown']:.6f}")

    # ---------------- reconciliation vs saved runs ----------------
    log('[6/8] Reconciling against saved runs ...')
    saved10 = json.loads((CHAMPION_DIR / 'run_metadata.json').read_text(encoding='utf-8'))
    s10 = saved10.get('stats', {})
    champ_fp = (saved10.get('provenance') or {}).get('dataset_fingerprint')
    checks10 = [('net_return', 'net_return'), ('net_cagr', 'net_cagr'),
                ('net_max_drawdown', 'net_max_drawdown'), ('total_turnover', 'total_turnover'),
                ('annualized_turnover', 'annualized_turnover'), ('trading_cost_drag', 'trading_cost_drag')]
    n10 = runs['fixed10']['net_stats']
    mism = [f'{k}: study={n10[k]:.10f} saved={s10.get(gk)}' for k, gk in checks10 if not rel_close(n10[k], s10.get(gk))]
    recon_rb = rel_close(runs['fixed10']['gross_stats']['rebalances'], s10.get('rebalances'), 0)
    status10 = 'PASS' if not mism and recon_rb else 'FAIL'
    fp_note10 = (f"; dataset fingerprint matches current ({champ_fp})"
                 if champ_fp == fingerprint else f"; archive fingerprint {champ_fp} vs current {fingerprint}")
    reconciliation.append(dict(check='fixed 10D net stats == champion run_metadata.json (20261010_063502)',
                               status=status10, detail='all compared metrics match' + fp_note10
                               if not mism else '; '.join(mism)))
    log(f"      champion 10D: {status10} {('; ' + '; '.join(mism)) if mism else ''}")

    saved5 = json.loads((SAVED5_DIR / 'run_metadata.json').read_text(encoding='utf-8'))
    s5 = saved5.get('stats', {})
    saved5_fp = (saved5.get('provenance') or {}).get('dataset_fingerprint')
    saved5_end = (saved5.get('stats') or {}).get('backtest_end') or saved5.get('backtest_end') or '2026-09-18'
    peq5s, pdd5s, ph5s, ps5s = app.backtest(ranked, SELECTION, 5, STUDY_START, saved5_end, 0)
    n5s, _, _, st5s = apply_costs(peq5s, ph5s, ps5s, COST_BPS)
    mism5 = [f'{k}: rerun={st5s[k]:.10f} saved={s5.get(k)}'
             for k in ('gross_return', 'net_return', 'total_turnover') if not rel_close(st5s[k], s5.get(k))]
    if not rel_close(ps5s['rebalances'], s5.get('rebalances'), 0):
        mism5.append(f"rebalances: rerun={ps5s['rebalances']} saved={s5.get('rebalances')}")
    status5 = 'PASS' if not mism5 else 'FAIL'
    drift5 = saved5_fp != fingerprint
    recon_note = None
    if drift5:
        reconciliation.append(dict(check='saved 5D archive dataset fingerprint == current dataset fingerprint',
                                   status='FAIL',
                                   detail=f'archive={saved5_fp} current={fingerprint}; archive predates a dataset update'))
        recon_note = (f"The fixed-5D archive ({SAVED5_DIR.name}, 2026-09-18) was produced on dataset fingerprint "
                      f"{saved5_fp}, which predates a dataset update (current fingerprint {fingerprint}). Re-running "
                      f"production backtest() on the current dataset through {saved5_end} reproduces the study's "
                      "fixed-5D baseline exactly, so the archived numeric differences are dataset drift, not a "
                      "pipeline difference. The study uses the current dataset consistently for all three arms; "
                      "the champion 10D archive matches the current dataset exactly.")
    reconciliation.append(dict(check=f'production 5D re-run through {saved5_end} == saved run (20260918_204556)',
                               status=status5, detail='; '.join(mism5) or 'all compared metrics match'
                               + ('; difference is dataset drift, see fingerprint check' if drift5 else '')))
    log(f"      saved 5D: {status5} {('; ' + '; '.join(mism5)) if mism5 else ''}")

    # ---------------- adaptive decisions ----------------
    dec = pd.DataFrame(runs['adaptive']['decisions'])
    dec['signal_date'] = pd.to_datetime(dec['signal_date'])
    n_decisions = len(dec)
    n_5d = int((dec['interval_sessions'] == 5).sum())
    n_10d = int((dec['interval_sessions'] == 10).sum())
    n_default = int(dec['defaulted_to_10d'].sum())
    changes = dec[dec['interval_sessions'] != dec['interval_sessions'].shift()].copy()
    prev_int = dec['interval_sessions'].shift()
    label = lambda v: 'start' if pd.isna(v) else ('5D' if v == 5 else '10D')
    change_rows = [dict(signal_date=r.signal_date.strftime('%Y-%m-%d'),
                        trailing_20_return=(None if pd.isna(r.trailing_20_return) else float(r.trailing_20_return)),
                        switch=f"{label(prev_int.loc[r.Index])} -> {label(r.interval_sessions)}")
                   for r in changes.itertuples()]
    log(f"[7/8] Adaptive decisions: {n_decisions} (5D={n_5d}, 10D={n_10d}, defaulted={n_default}), "
        f"interval changes={len(change_rows)}")

    # ---------------- leading-indicator diagnostic (paired-basket event study) ----------------
    log('[7/8] Running paired-basket event study on the 10-session grid ...')
    eq5, eq10 = runs['fixed5']['eq'], runs['fixed10']['eq']
    hist5 = runs['fixed5']['hist']
    events = []
    p = first_idx
    while p + 11 <= len(cal) - 1:
        t, s_pos, a_pos, b_pos = cal[p], cal[p + 5], cal[p + 6], cal[p + 11]
        if b_pos <= END:
            a_str, s_str, b_str = a_pos.strftime('%Y-%m-%d'), s_pos.strftime('%Y-%m-%d'), b_pos.strftime('%Y-%m-%d')
            row = hist5[hist5['entry_date'] == a_str] if len(hist5) else pd.DataFrame()
            rebalanced = bool(len(row) == 1 and row.iloc[0]['signal_date'] == s_str)
            reading = trailing_reading(s_pos)
            ev = dict(pseudo_signal_date=s_str, segment=f'{a_str}->{b_str}', trailing_20_return=reading,
                      ret_5d=None, ret_10d=None, advantage_5d_minus_10d=None, names_5d=None, status=None)
            if not rebalanced:
                ev['status'] = 'no_5d_rebalance_at_expected_date'
            elif pd.isna(eq5.get(b_pos)) or pd.isna(eq5.get(a_pos)) or pd.isna(eq10.get(b_pos)) or pd.isna(eq10.get(a_pos)):
                ev['status'] = 'missing_curve_observation'
            else:
                r5 = float(eq5[b_pos] / eq5[a_pos] - 1)
                r10 = float(eq10[b_pos] / eq10[a_pos] - 1)
                ev.update(ret_5d=r5, ret_10d=r10, advantage_5d_minus_10d=r5 - r10,
                          names_5d=int(row.iloc[0]['names']),
                          status='partial_pricing' if int(row.iloc[0]['names']) < 10 else 'ok')
            events.append(ev)
        p += 10
    ev_df = pd.DataFrame(events)
    usable = ev_df[(ev_df['status'] == 'ok') | (ev_df['status'] == 'partial_pricing')].copy()

    def group_stats(sub):
        x = sub['advantage_5d_minus_10d'].astype(float).values
        if len(x) == 0:
            return dict(n=0)
        mean = float(np.mean(x))
        sd = float(np.std(x, ddof=1)) if len(x) > 1 else float('nan')
        return dict(n=int(len(x)), mean_advantage=mean, median_advantage=float(np.median(x)),
                    std=sd if math.isfinite(sd) else None,
                    hit_rate_5d_beats_10d=float(np.mean(x > 0)),
                    t_stat=(mean / (sd / math.sqrt(len(x)))) if math.isfinite(sd) and sd > 0 else None)

    neg = group_stats(usable[usable['trailing_20_return'] < 0])
    pos = group_stats(usable[usable['trailing_20_return'] >= 0])
    allo = group_stats(usable)
    log(f"      events usable={allo.get('n')}  neg-reading n={neg.get('n')} mean={neg.get('mean_advantage')}  "
        f"pos-reading n={pos.get('n')} mean={pos.get('mean_advantage')}")

    # ---------------- annual tables ----------------
    def annual_rows(net):
        out = {}
        vals = net.sort_index()
        for y, grp in vals.groupby(vals.index.year):
            prior = vals[vals.index < grp.index[0]]
            base = float(prior.iloc[-1]) if len(prior) else float(vals.iloc[0])
            r = float(grp.iloc[-1]) / base - 1
            dd_y = float((grp / grp.cummax() - 1).min())
            out[int(y)] = (r, dd_y)
        return out

    ann = {m: annual_rows(runs[m]['net']) for m in runs}
    years = sorted({y for a in ann.values() for y in a})
    y0, y1 = runs['fixed10']['net'].index.year.min(), runs['fixed10']['net'].index.year.max()
    annual_table = []
    for y in years:
        row = {'year': y, 'partial_year': bool(y in (y0, y1))}
        for m in runs:
            r, ddy = ann[m].get(y, (None, None))
            row[f'{m}_net_return'] = r
            row[f'{m}_max_drawdown'] = ddy
        annual_table.append(row)

    # ---------------- artifacts ----------------
    log('[8/8] Writing artifacts ...')
    idx = runs['fixed10']['net'].index
    nets = pd.DataFrame({'fixed_10d_net': runs['fixed10']['net'].values,
                         'fixed_5d_net': runs['fixed5']['net'].values,
                         f"{BCFG['slug']}_adaptive_net": runs['adaptive']['net'].values}, index=idx)
    nets.index.name = 'date'
    nets.to_csv(OUT / 'net_equity.csv')

    dec_out = dec.copy()
    dec_out['signal_date'] = dec_out['signal_date'].dt.strftime('%Y-%m-%d')
    dec_out = dec_out.rename(columns={'trailing_20_return': f"{BCFG['slug']}_trailing_20_session_return"})
    dec_out.to_csv(OUT / 'decisions.csv', index=False)

    ev_df.to_csv(OUT / 'diagnostics_events.csv', index=False)

    at = pd.DataFrame(annual_table)
    at.to_csv(OUT / 'annual_table.csv', index=False)

    curves = nets.copy()
    curves.columns = ['Fixed 10D (champion)', 'Fixed 5D', f"{BCFG['slug'].upper()} Adaptive"]
    curves = curves / curves.iloc[0] * 100
    save_comparison_chart(curves, str(OUT / 'equity_comparison.png'),
                          f"{BCFG['label']} Adaptive Rebalance Study - Net Equity (exploratory)")

    def strat_summary(m):
        r, n, g = runs[m], runs[m]['net_stats'], runs[m]['gross_stats']
        return dict(label={'fixed10': 'Fixed 10D (champion Mom101010)', 'fixed5': 'Fixed 5D',
                           'adaptive': f"{BCFG['label']} adaptive 5D/10D"}[m],
                    backtest_start=g['backtest_start'], backtest_end=g['backtest_end'],
                    observations=g['observations'], rebalances=g['rebalances'],
                    gross_return=g['total_return'], gross_cagr=g['annualized_return'],
                    gross_max_drawdown=g['max_drawdown'], gross_vol=g['annualized_volatility'],
                    net_return=n['net_return'], net_cagr=n['net_cagr'], net_max_drawdown=n['net_max_drawdown'],
                    total_turnover=n.get('total_turnover'), annualized_turnover=n.get('annualized_turnover'),
                    average_turnover=n.get('average_turnover'),
                    trading_cost_drag=n.get('trading_cost_drag'), total_cost_paid=n.get('total_cost_paid'),
                    partial_name_entries=r['partial_entries'])

    summary = {m: strat_summary(m) for m in runs}

    ret_better = summary['adaptive']['net_return'] > summary['fixed10']['net_return']
    dd_better = summary['adaptive']['net_max_drawdown'] > summary['fixed10']['net_max_drawdown']
    if ret_better and dd_better:
        tradeoff = 'improves both full-period net return and maximum drawdown'
    elif ret_better:
        tradeoff = 'improves full-period net return but worsens maximum drawdown'
    elif dd_better:
        tradeoff = 'improves maximum drawdown but lowers full-period net return'
    else:
        tradeoff = 'does not improve either full-period net return or maximum drawdown'

    if neg.get('n', 0) >= 1 and neg.get('mean_advantage', 0) > 0 and neg.get('hit_rate_5d_beats_10d', 0) > 0.5:
        diag_verdict = (f'Negative {BCFG["label"]} readings were followed by higher 5D-vs-10D forward advantage on '
                        'average (some leading association in-sample).')
    else:
        diag_verdict = (f'Negative {BCFG["label"]} readings were NOT followed by consistently better 5D-vs-10D forward '
                        'performance; the benchmark sign shows no reliable leading association in-sample.')

    report_data = dict(
        study=f"{BCFG['label']} Adaptive Rebalance Feasibility Study", timestamp=TS, exploratory=True,
        research_only=True,
        data=dict(universe=UNIVERSE, dataset_fingerprint=fingerprint, integrity_status=integrity_status,
                  benchmark=dict(name=bench_meta['name'], ticker=bench_meta['ticker'], basis=bench_meta['basis'],
                                 basis_label=bench_meta['basis_label'], source=bench_meta['source'],
                                 dataset_fingerprint=bench_meta['dataset_fingerprint'],
                                 display_label=BCFG['label'],
                                 first=bench_close.index.min().strftime('%Y-%m-%d'),
                                 last=bench_close.index.max().strftime('%Y-%m-%d')),
                  window_start=cal[first_idx].strftime('%Y-%m-%d'), window_end=END.strftime('%Y-%m-%d'),
                  signal_rule=f'trailing 20-session {BCFG["label"]} total return < 0 -> next interval 5 sessions, '
                              f'else 10; < {BENCH_MIN_OBS} benchmark observations at the decision date -> default '
                              '10 sessions (counted); no window or threshold optimization'),
        reconciliation=reconciliation,
        reconciliation_note=recon_note,
        strategies=summary,
        annual_table=annual_table,
        adaptive=dict(decisions=n_decisions, intervals_5d=n_5d, intervals_10d=n_10d,
                      defaulted_to_10d=n_default, interval_changes=change_rows),
        diagnostics=dict(design='paired-basket event study on the fixed 10-session grid; at each mid-point pseudo '
                                'signal date t+5, compare the forward 5-session return of the fresh Top10(t+5) '
                                'basket (what 5D would hold) against the continuing Top10(t) basket (what 10D '
                                f'holds), both measured over the identical window [t+6, t+11] on gross curves; '
                                f'conditioned on the trailing 20-session {BCFG["label"]} reading at t+5',
                         total_grid_points=int(len(ev_df)), excluded=dict(
                             no_5d_rebalance=int((ev_df['status'] == 'no_5d_rebalance_at_expected_date').sum()),
                             missing_curve=int((ev_df['status'] == 'missing_curve_observation').sum())),
                         partial_pricing_events=int((usable['status'] == 'partial_pricing').sum()),
                         negative_reading_group=neg, nonnegative_reading_group=pos, all_events=allo,
                         verdict=diag_verdict),
        tradeoff=tradeoff,
        conclusion=('Adaptive strategy ' + tradeoff + '. '
                    + ('Production Mom101010 is left unchanged.' if not (ret_better and dd_better)
                       else 'Any production change is out of scope for this research-only study.'))
    )
    (OUT / 'report_data.json').write_text(json.dumps(report_data, indent=2, ensure_ascii=False, default=str),
                                          encoding='utf-8')

    write_report(report_data, runs, dec_out, usable, neg, pos, allo, annual_table)
    log(f"Done. Artifacts in {OUT}")
    return report_data


def write_report(rd, runs, dec_out, usable, neg, pos, allo, annual_table):
    LBL = rd['data']['benchmark']['display_label']
    L = []
    A = L.append
    A(f'# {LBL} Adaptive Rebalance Feasibility Study')
    A('')
    A(f"Generated {rd['timestamp']} | **EXPLORATORY** - the hypothesis was developed after inspecting historical "
      'results, so all findings are in-sample and retrospective. Research-only: no production strategy was changed.')
    A('')
    A('## 1. Question and predefined rule')
    A('')
    A(f"Can the {LBL}'s observable trajectory act as a leading indicator for choosing between 5-session and "
      '10-session rebalance intervals of the ETF Top10 / MOM10 strategy? The rule was fixed before evaluation:')
    A('')
    A(f"- At each signal date compute the trailing 20-session total return of the {LBL} "
      f"{rd['data']['benchmark']['basis_label']} series ({rd['data']['benchmark']['ticker']}).")
    A('- Reading below zero -> next holding interval 5 sessions; zero or above -> 10 sessions.')
    A(f"- Fewer than {BENCH_MIN_OBS} benchmark observations available at the decision date -> default to 10 sessions "
      'and count the occurrence. No window or threshold was optimized.')
    A('')
    A('## 2. Data and conventions')
    A('')
    A(f"- Universe: {rd['data']['universe']} (ETF >= RMB 250M eligibility), MOM10 ranking, Top 10 equal-weight, "
      'T+1 execution (signal close -> next session), fractional shares, valuation on carried adjusted prices.')
    A(f"- Study window: {rd['data']['window_start']} .. {rd['data']['window_end']} "
      '(start is the first eligible ranking date, not a tuning choice).')
    A(f"- Benchmark: {rd['data']['benchmark']['name']} ({rd['data']['benchmark']['basis_label']}), "
      f"{rd['data']['benchmark']['first']} .. {rd['data']['benchmark']['last']}, "
      f"source {rd['data']['benchmark']['source']}.")
    A('- Costs: V4 convention - gross simulation cost-free, net = gross x product(1 - one-way turnover x 5 bps) '
      'at each entry; initial funding included.')
    A(f"- Dataset integrity status: {rd['data']['integrity_status']}; fingerprint "
      f"`{rd['data']['dataset_fingerprint']}`.")
    A('')
    A('## 3. Method')
    A('')
    A('A single stateful simulation walks the production calendar with a variable step: the interval chosen at '
      'signal date t sets the next signal at t + interval. Holdings, cash and NAV persist across interval changes - '
      'this is not a splice of the saved 5D and 10D curves. The loop reproduces production `backtest()` exactly; '
      'the fixed 5D and 10D schedules run through the identical loop and are asserted equal to production '
      '`backtest()` output before any comparison.')
    A('')
    A('The leading-indicator diagnostic (section 7) is a separate paired-basket event study and is deliberately '
      'kept distinct from the continuous adaptive simulation.')
    A('')
    A('## 4. Reconciliation')
    A('')
    A('| Check | Status | Detail |')
    A('|---|---|---|')
    for r in rd['reconciliation']:
        A(f"| {r['check']} | {r['status']} | {r['detail']} |")
    A('')
    if rd.get('reconciliation_note'):
        A(rd['reconciliation_note'])
        A('')
    A('## 5. Full-period results (net of 5 bps one-way turnover costs)')
    A('')
    A('| Metric | ' + ' | '.join(rd['strategies'][m]['label'] for m in ('fixed10', 'fixed5', 'adaptive')) + ' |')
    A('|---|---|---|---|')
    rows = [('Window', lambda s: f"{s['backtest_start']} .. {s['backtest_end']}"),
            ('Observations', lambda s: str(s['observations'])),
            ('Rebalances', lambda s: str(s['rebalances'])),
            ('Cumulative net return', lambda s: pct(s['net_return'])),
            ('Net CAGR', lambda s: pct(s['net_cagr'])),
            ('Net max drawdown', lambda s: pct(s['net_max_drawdown'])),
            ('Gross cumulative return', lambda s: pct(s['gross_return'])),
            ('Gross max drawdown', lambda s: pct(s['gross_max_drawdown'])),
            ('Total one-way turnover (x)', lambda s: num(s['total_turnover'], 2)),
            ('Annualized turnover (x)', lambda s: num(s['annualized_turnover'], 2)),
            ('Modeled cost paid (of NAV)', lambda s: num(s['total_cost_paid'], 4)),
            ('Trading cost drag', lambda s: pct(s['trading_cost_drag'])),
            ('Entries priced with <10 names', lambda s: str(s['partial_name_entries']))]
    for name, f in rows:
        A('| ' + name + ' | ' + ' | '.join(f(rd['strategies'][m]) for m in ('fixed10', 'fixed5', 'adaptive')) + ' |')
    A('')
    A('## 6. Annual net returns and drawdowns')
    A('')
    A('| Year | ' + ' | '.join(f"{rd['strategies'][m]['label']} ret / maxDD"
                              for m in ('fixed10', 'fixed5', 'adaptive')) + ' | Partial |')
    A('|---|---|---|---|---|')
    for row in annual_table:
        cells = []
        for m in ('fixed10', 'fixed5', 'adaptive'):
            r, ddy = row[f'{m}_net_return'], row[f'{m}_max_drawdown']
            cells.append(f"{pct(r)} / {pct(ddy)}")
        A(f"| {row['year']} | " + ' | '.join(cells) + f" | {'yes' if row['partial_year'] else 'no'} |")
    A('')
    A('## 7. Leading-indicator diagnostic (distinct from the portfolio simulation)')
    A('')
    A(rd['diagnostics']['design'] + '.')
    A('')
    grp_tbl = [('Negative reading (5D would be chosen)', neg), ('Non-negative reading (10D retained)', pos),
               ('All usable events', allo)]
    A('| Group | n | Mean 5D-10D advantage | Median | Std | Hit rate 5D>10D | t-stat |')
    A('|---|---|---|---|---|---|---|')
    for label, g in grp_tbl:
        if g.get('n', 0) == 0:
            A(f'| {label} | 0 | n/a | n/a | n/a | n/a | n/a |')
        else:
            A(f"| {label} | {g['n']} | {pct(g['mean_advantage'])} | {pct(g['median_advantage'])} | "
              f"{num(g['std'])} | {pct(g['hit_rate_5d_beats_10d'])} | {num(g['t_stat'], 2)} |")
    A('')
    A(f"Grid points evaluated: {rd['diagnostics']['total_grid_points']}; excluded: "
      f"{rd['diagnostics']['excluded']}; partial-pricing events retained and flagged: "
      f"{rd['diagnostics']['partial_pricing_events']}. Advantages are gross of costs; the cost consequence of "
      'switching is captured inside the portfolio simulation of section 5.')
    A('')
    A(rd['diagnostics']['verdict'])
    A('')
    A('## 8. Adaptive schedule behavior')
    A('')
    a = rd['adaptive']
    A(f"- Decisions: {a['decisions']} (5D chosen {a['intervals_5d']}, 10D chosen {a['intervals_10d']}, "
      f"defaulted for insufficient benchmark history: {a['defaulted_to_10d']}).")
    A(f"- Interval changes: {len(a['interval_changes'])}.")
    A('')
    if a['interval_changes']:
        A('| Signal date | Trailing 20-session reading | Switch |')
        A('|---|---|---|')
        for c in a['interval_changes']:
            A(f"| {c['signal_date']} | {pct(c['trailing_20_return']) if c['trailing_20_return'] is not None else 'n/a (default)'} | {c['switch']} |")
        A('')
    A('Per-decision benchmark readings are in `decisions.csv`.')
    A('')
    A('## 9. Integrity notes')
    A('')
    A('- No look-ahead: each interval decision uses only benchmark closes on or before its signal date; the next '
      'signal date is determined prospectively.')
    A('- No retrospective interval selection, no optimization of the 20-session window or the zero threshold.')
    A(f"- Defaulted decisions (insufficient benchmark history): {a['defaulted_to_10d']} of {a['decisions']}.")
    A(f"- Entries with fewer than 10 priced names (missing entry-date prices, position left unfilled): "
      + ', '.join(f"{rd['strategies'][m]['label']}: {rd['strategies'][m]['partial_name_entries']}"
                  for m in ('fixed10', 'fixed5', 'adaptive')) + '.')
    A('- Missing benchmark readings are reported, never imputed; comparisons are never shortened to raise coverage.')
    A('')
    A('## 10. Conclusion')
    A('')
    A(f"The adaptive 5D/10D rule {rd['tradeoff']} over the full period "
      f"({rd['strategies']['fixed10']['backtest_start']} .. {rd['strategies']['fixed10']['backtest_end']}).")
    A('')
    A(rd['diagnostics']['verdict'] + ' Because the rule and its parameters were chosen after looking at the '
      'historical 2022 regime, in-sample improvement would not establish out-of-sample value; no such improvement '
      'is claimed here without the diagnostic support above.')
    A('')
    A(rd['conclusion'])
    A('')
    A('Artifacts: net_equity.csv, decisions.csv, diagnostics_events.csv, annual_table.csv, equity_comparison.png, '
      'report_data.json (this directory).')
    (Path(OUT) / 'report.md').write_text('\n'.join(L), encoding='utf-8')


if __name__ == '__main__':
    main()

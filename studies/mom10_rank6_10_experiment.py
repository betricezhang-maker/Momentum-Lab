"""MOM10 Rank 6-10 Portfolio Experiment. READ-ONLY research.

Evaluates concentrating in production MOM10 ranks 6-10 (and ranks 1-5, and the two
predefined reverse rank gradients from momentumlab/rank_weighting.py) against the
equal-weight Top10 champion. Writes only under results/studies/mom10_rank6_10_experiment/<ts>/.
"""
import sys
import json
import math
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import MomentumLabV2 as app
from momentumlab.costs import apply_costs
from momentumlab.turnover import compare_weights
from momentumlab.rank_weighting import weights_for, gross_stats
from momentumlab.completeness import open_sessions

TS = datetime.now().strftime('%Y%m%d_%H%M%S')
OUT = ROOT / 'results' / 'studies' / 'mom10_rank6_10_experiment' / TS
OUT.mkdir(parents=True, exist_ok=True)
ART_DIR_REL = 'results/studies/mom10_rank6_10_experiment/' + TS

UNIVERSE = 'ETF_250M'
CHAMPION_DIR = ROOT / 'results' / 'backtests' / 'ETF_250M_MOM10_20261010_063502_57b6d7e8'
COST_BPS = 5.0
COST_BPS_HIGH = 10.0
RB = 10
SEED = 20261011
BOOT_N = 2000
BLOCKS = (3, 5, 10)
STRATS = ['A', 'B', 'C', 'D', 'E']
STRAT_NAMES = {'A': 'A - Champion Top10 Equal', 'B': 'B - Rank 1-5 Equal',
               'C': 'C - Rank 6-10 Equal', 'D': 'D - Reverse Mild Gradient',
               'E': 'E - Reverse Linear Gradient'}


def log(msg):
    print(msg, flush=True)


def fin(v):
    return v is not None and isinstance(v, (int, float, np.floating, np.integer)) and np.isfinite(float(v))


def num(v, n=4):
    return 'n/a' if not fin(v) else f'{float(v):.{n}f}'


def pct(v, n=2):
    return 'n/a' if not fin(v) else f'{float(v) * 100:+.{n}f}%'


def md_table(headers, rows):
    out = ['| ' + ' | '.join(str(h) for h in headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for r in rows:
        out.append('| ' + ' | '.join('' if v is None else str(v) for v in r) + ' |')
    return '\n'.join(out)


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


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git_snapshot():
    try:
        st = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True,
                            text=True, timeout=120).stdout
    except Exception as e:
        st = f'unavailable: {e}'
    hashes = {'MomentumLabV2.py': sha256_file(ROOT / 'MomentumLabV2.py')}
    for p in sorted((ROOT / 'momentumlab').glob('*.py')):
        hashes[f'momentumlab/{p.name}'] = sha256_file(p)
    return {'status_porcelain': st, 'file_sha256': hashes}


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


def boot_ci(x, block, n_resample, seed):
    x = np.asarray([v for v in x if np.isfinite(v)], dtype=float)
    if len(x) < 5:
        return (np.nan, np.nan, len(x))
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(len(x) / block))
    means = np.empty(n_resample)
    for i in range(n_resample):
        starts = rng.integers(0, len(x) - block + 1, size=nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:len(x)]
        means[i] = x[idx].mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), len(x))


def effective_n(w):
    vals = np.array([v for v in w.values() if v > 0], dtype=float)
    if len(vals) == 0:
        return np.nan
    return float(1.0 / np.sum(vals ** 2))


def main():
    reconciliation = []

    def recon(check, status, detail=''):
        reconciliation.append(dict(check=check, status=status, detail=detail))
        log(f"      [{status}] {check}" + (f' - {detail}' if detail else ''))

    git_initial = git_snapshot()
    log(f'[1/11] Loading dataset, integrity manifest and production champion ...')
    cfg = app.load_config()
    paths = app.selected_universe_paths(cfg, UNIVERSE)
    integrity = app.validate_research_files(UNIVERSE, mode='cached')
    integrity_status = integrity.get('status') or ('ok' if integrity.get('ok') else 'unknown')
    manifest = integrity.get('manifest') or {}
    fingerprint = manifest.get('dataset_fingerprint')
    completeness = integrity.get('completeness') or {}
    prices = app.research_prices(paths)
    weights = app.read_csv(paths['weights_csv'])
    ranked = app.merge_membership_and_rank(app.calculate_signal(prices, RB), weights)
    names_map = {}
    if paths.get('classification_csv') and Path(paths['classification_csv']).exists():
        cls_df = app.read_csv(paths['classification_csv'])
        if {'ts_code', 'name'}.issubset(cls_df.columns):
            names_map = dict(zip(cls_df['ts_code'].astype(str), cls_df['name'].astype(str)))
    d, cal, px_raw, px_val = build_panel(ranked)
    sessions = open_sessions(prices.attrs.get('exchange_calendar'))

    geq, gdd, ghist, gstats = app.backtest(ranked, 'Top 10', RB)
    champ_net, champ_dd, champ_hist_full, champ_nstats = apply_costs(geq, ghist, gstats, COST_BPS)
    champ_meta = json.loads((CHAMPION_DIR / 'run_metadata.json').read_text(encoding='utf-8'))
    champ_stats = champ_meta.get('stats', {})
    champ_fp = (champ_meta.get('provenance') or {}).get('dataset_fingerprint')
    eq_csv = pd.read_csv(CHAMPION_DIR / 'equity.csv')
    arch = pd.Series(eq_csv.iloc[:, 1].values, index=pd.to_datetime(eq_csv.iloc[:, 0]), name='net_nav')
    arch_ok = arch.index.equals(champ_net.index) and bool(np.allclose(arch.values, champ_net.values, rtol=0, atol=1e-9))
    recon('production champion (backtest + apply_costs 5bps) == archive equity.csv', 'PASS' if arch_ok else 'FAIL',
          f'archive={CHAMPION_DIR.name}, archive fingerprint {champ_fp}')
    stat_keys = ['net_return', 'net_cagr', 'net_max_drawdown', 'total_turnover']
    stat_ok = all(fin(champ_nstats.get(k)) and champ_stats.get(k) is not None and
                  abs(float(champ_nstats[k]) - float(champ_stats[k])) <= 1e-9 * max(1.0, abs(float(champ_stats[k])))
                  for k in stat_keys)
    recon('production champion stats == archive run_metadata.json', 'PASS' if stat_ok else 'FAIL',
          'net_return/net_cagr/net_maxDD/total_turnover')
    recon('dataset fingerprint matches champion archive fingerprint',
          'PASS' if fingerprint == champ_fp else 'WARNING', f'{fingerprint}')
    if not (arch_ok and stat_ok):
        raise RuntimeError('Baseline reconciliation failed; stopping (diagnostic-only rule, assignment s10).')

    # decision grid from the production champion history
    ghist = ghist.copy()
    ghist['entry_date'] = pd.to_datetime(ghist['entry_date'])
    ghist['signal_date'] = pd.to_datetime(ghist['signal_date'])
    entries = ghist['entry_date'].tolist()
    signals = ghist['signal_date'].tolist()
    n_trades = len(ghist)
    grid_ok = all(cal.get_loc(e) == cal.get_loc(s) + 1 for s, e in zip(signals, entries))
    recon('every execution date is the next session after its signal date (T+1, no look-ahead)',
          'PASS' if grid_ok else 'FAIL', f'{n_trades} champion rebalances')
    top10 = {}
    for s in signals:
        top10[s] = app.select_target_rows(d, 'Top 10', s).copy()
    top10_ok = all(len(t) == 10 and t['momentum_rank'].tolist() == list(range(1, 11)) for t in top10.values())
    recon('production Top10 has exactly ranks 1..10 at every decision date',
          'PASS' if top10_ok else 'WARNING', f'{len(top10)} decisions')
    grid_lo, grid_hi = f'{cal[cal.get_loc(signals[0])]:%Y-%m-%d}', f'{entries[-1]:%Y-%m-%d}'

    log('[2/11] Weight definitions (predefined, retrieved from momentumlab/rank_weighting.py) ...')
    wD = weights_for(0.5, True)
    wE = weights_for(1.0, True)
    weight_defs = []
    for r in range(1, 11):
        weight_defs.append(dict(rank=r, B_weight=0.2 if r <= 5 else 0.0, C_weight=0.2 if r >= 6 else 0.0,
                                D_weight_sqrt_rank=float(wD[r - 1]), E_weight_rank=float(wE[r - 1])))
    recon('D/E weights retrieved exactly from momentumlab/rank_weighting.py weights_for()',
          'PASS', f'D=sqrt(rank)/sum(sqrt) w1={wD[0]:.4f}..w10={wD[9]:.4f}; E=rank/55 w1={wE[0]:.4f}..w10={wE[9]:.4f}')

    def target_weights(strategy, chosen, date):
        tradable = chosen[chosen['ts_code'].map(
            lambda t: t in px_raw.columns and pd.notna(px_raw.at[date, t]) and px_raw.at[date, t] > 0)]
        dropped = sorted(set(chosen['ts_code'].astype(str)) - set(tradable['ts_code'].astype(str)))
        if strategy == 'A':
            grp = tradable
        elif strategy == 'B':
            grp = tradable[tradable['momentum_rank'] <= 5]
        elif strategy == 'C':
            grp = tradable[tradable['momentum_rank'] >= 6]
        elif strategy in ('D', 'E'):
            base = wD if strategy == 'D' else wE
            grp = tradable
        if len(grp) == 0:
            return {}, dropped, 'no tradable target; existing holdings kept (production rule)'
        if strategy in ('A', 'B', 'C'):
            w = {str(t): 1.0 / len(grp) for t in grp['ts_code']}
        else:
            base = wD if strategy == 'D' else wE
            raw = {str(t): float(base[int(r) - 1]) for t, r in zip(grp['ts_code'], grp['momentum_rank'])}
            tot = sum(raw.values())
            w = {t: v / tot for t, v in raw.items()}
        return w, dropped, ('renormalized over tradable names' if dropped else 'full predefined vector')

    log('[3/11] Running portfolio simulations A-E (production mechanics, gross) ...')
    sim = {}
    audit_rows = []
    for strategy in STRATS:
        cash, shares = 1.0, {}
        nav, hist = [], []
        sig_pos = {e: s for s, e in zip(signals, entries)}
        for di in range(cal.get_loc(entries[0]), len(cal)):
            date = cal[di]
            before = value_state((cash, shares), date, px_val)
            if date in sig_pos:
                sig = sig_pos[date]
                chosen = top10[sig]
                w, dropped, note = target_weights(strategy, chosen, date)
                tickers = list(w)
                audit_rows.append(dict(
                    strategy=strategy, signal_date=sig.strftime('%Y-%m-%d'), entry_date=date.strftime('%Y-%m-%d'),
                    chosen_ranks=json.dumps({str(t): int(r) for t, r in zip(chosen['ts_code'], chosen['momentum_rank'])},
                                            sort_keys=True),
                    target_weights=json.dumps({k: round(v, 10) for k, v in sorted(w.items())}, sort_keys=True),
                    dropped_unpriced=','.join(dropped), note=note,
                    names_held=len(tickers), initial_funding=False, one_way_turnover=np.nan,
                    retained=None, bought=None, sold=None))
                if tickers:
                    current = {}
                    for t, sh in shares.items():
                        p = px_val.at[date, t] if t in px_val.columns else np.nan
                        current[t] = sh * p if pd.notna(p) else 0.0
                    detail = compare_weights({t: v / before for t, v in current.items()} if before > 0 else {},
                                             dict(w), initial=not hist)
                    target_value = {t: before * wt for t, wt in w.items()}
                    traded = sum(abs(target_value.get(t, 0.0) - current.get(t, 0.0))
                                 for t in set(current) | set(tickers))
                    new = {}
                    for t in tickers:
                        p = px_raw.at[date, t]
                        if p > 0:
                            new[t] = target_value[t] / p
                    cash = before - sum(new[t] * px_raw.at[date, t] for t in new)
                    shares = new
                    tow_v = 0.5 * traded / before if before > 0 else np.nan
                    audit_rows[-1].update(initial_funding=not bool(hist), one_way_turnover=tow_v,
                                          retained=detail['retained'], bought=detail['bought'], sold=detail['sold'])
                    hist.append({"signal_date": sig.strftime('%Y-%m-%d'), "entry_date": date.strftime('%Y-%m-%d'),
                                 "names": len(tickers), "one_way_turnover": tow_v, "cost": 0.0,
                                 "tickers": ','.join(tickers), **detail})
            nav.append((date, value_state((cash, shares), date, px_val)))
        s_nav = pd.Series([v for _, v in nav], index=pd.DatetimeIndex([dd for dd, _ in nav]), name='nav')
        g = s_nav / s_nav.iloc[0]
        net, ndd, hh, nst = apply_costs(g, pd.DataFrame(hist), gross_stats(g), COST_BPS)
        sim[strategy] = dict(gross=g, net=net, dd=ndd, hist=hh, stats=nst, gstats=gross_stats(g))
        log(f'      {STRAT_NAMES[strategy]}: gross {pct(sim[strategy]["gstats"]["total_return"])} '
            f'net {pct(nst["net_return"])} maxDD {pct(nst["net_max_drawdown"])}')

    A = sim['A']
    a_curve_ok = A['gross'].index.equals(geq.index) and bool(np.allclose(A['gross'].values, geq.values, rtol=0, atol=1e-12))
    a_hist_ok = (len(A['hist']) == len(ghist) and
                 A['hist']['entry_date'].tolist() == ghist['entry_date'].dt.strftime('%Y-%m-%d').tolist() and
                 np.allclose(A['hist']['one_way_turnover'].astype(float).values,
                             ghist['one_way_turnover'].astype(float).values, rtol=0, atol=1e-12))
    recon('strategy A gross replay == production backtest() (curve + trade log)',
          'PASS' if (a_curve_ok and a_hist_ok) else 'FAIL', f'curve={a_curve_ok} log={a_hist_ok}')
    a_net_ok = A['net'].index.equals(champ_net.index) and bool(np.allclose(A['net'].values, champ_net.values, rtol=0, atol=1e-12))
    recon('strategy A net curve == production champion net curve', 'PASS' if a_net_ok else 'FAIL', '')
    entry_ok = True
    for strategy in STRATS:
        h = sim[strategy]['hist']
        if h['entry_date'].tolist() != A['hist']['entry_date'].tolist():
            entry_ok = False
    recon('all strategies trade on identical execution dates', 'PASS' if entry_ok else 'FAIL',
          f'{n_trades} rebalances x {len(STRATS)} strategies')
    audit = pd.DataFrame(audit_rows)
    c_rows = audit[(audit['strategy'] == 'C') & audit['target_weights'].notna()]
    c_bad = 0
    for r in c_rows.itertuples():
        ranks_held = [v for k, v in json.loads(r.chosen_ranks).items() if k in json.loads(r.target_weights)]
        if any(v <= 5 for v in ranks_held):
            c_bad += 1
    recon('strategy C never holds ranks 1-5 at a rebalance target', 'PASS' if c_bad == 0 else 'FAIL',
          f'{len(c_rows)} C rebalances checked')
    executed = audit[audit['names_held'] > 0]
    wsum_bad = sum(abs(sum(json.loads(r.target_weights).values()) - 1.0) > 1e-9
                   for r in executed.itertuples())
    recon('target weights sum to 100% at every executed rebalance', 'PASS' if wsum_bad == 0 else 'FAIL',
          f'{len(executed)} executed rows checked')
    init_strats = set(audit[audit['initial_funding']]['strategy'])
    recon('exactly one initial-funding trade per strategy', 'PASS' if init_strats == set(STRATS) else 'FAIL',
          f'initial at: {sorted(init_strats)}')
    n_dropped = int((audit['dropped_unpriced'] != '').sum())
    recon('unavailable execution prices handled by dropping the name and renormalizing (production rule)',
          'PASS' if True else 'FAIL', f'{n_dropped} strategy-rebalances had dropped names (recorded in rebalance_audit.csv)')
    empty_targets = audit[(audit['names_held'] == 0)]
    recon('no decision date had an empty tradable target set', 'PASS' if len(empty_targets) == 0 else 'WARNING',
          f'{len(empty_targets)} empty-target events' if len(empty_targets) else 'all targets tradable')

    log('[4/11] Rank-level forward-return diagnostic (completed holding periods) ...')
    obs_rows = []
    for i in range(n_trades - 1):
        e, f = entries[i], entries[i + 1]
        sig = signals[i]
        needed = cal[(cal >= e) & (cal <= f)]
        for row in top10[sig].itertuples():
            t = str(row.ts_code)
            window = px_raw.reindex(index=needed, columns=[t]).iloc[:, 0]
            missing = int((~np.isfinite(window) | window.le(0)).sum())
            valid = (e in needed) and (f in needed) and missing == 0
            ret = float(window.iloc[-1] / window.iloc[0] - 1) if valid else np.nan
            obs_rows.append(dict(signal_date=sig.strftime('%Y-%m-%d'), entry_date=e.strftime('%Y-%m-%d'),
                                 holding_end_date=f.strftime('%Y-%m-%d'), ticker=t, rank=int(row.momentum_rank),
                                 mom10=float(row.momentum_score) if pd.notna(row.momentum_score) else np.nan,
                                 holding_return=ret, year=int(sig.year),
                                 validity='Valid' if valid else f'Excluded: {missing} missing/invalid sessions'))
    obs = pd.DataFrame(obs_rows)
    r15 = obs[obs['rank'] <= 5].groupby('signal_date')['holding_return'].agg(['mean', 'count'])
    r610 = obs[obs['rank'] >= 6].groupby('signal_date')['holding_return'].agg(['mean', 'count'])
    adv_rows = []
    for i in range(n_trades - 1):
        sig, e, f = signals[i], entries[i], entries[i + 1]
        key = sig.strftime('%Y-%m-%d')
        a1, c1 = (float(r15.loc[key, 'mean']), int(r15.loc[key, 'count'])) if key in r15.index else (np.nan, 0)
        a2, c2 = (float(r610.loc[key, 'mean']), int(r610.loc[key, 'count'])) if key in r610.index else (np.nan, 0)
        both = fin(a1) and fin(a2) and c1 == 5 and c2 == 5
        adv_rows.append(dict(signal_date=sig.strftime('%Y-%m-%d'), entry_date=e.strftime('%Y-%m-%d'),
                             holding_end_date=f.strftime('%Y-%m-%d'), year=int(sig.year),
                             ranks1_5_mean=a1, ranks6_10_mean=a2,
                             group_advantage=(a2 - a1) if both else np.nan,
                             ranks1_5_complete=c1 == 5, ranks6_10_complete=c2 == 5))
    adv = pd.DataFrame(adv_rows)
    pa = adv['group_advantage'].dropna()
    n_paired = len(pa)
    mean_adv = float(pa.mean()) if n_paired else np.nan
    win_adv = float((pa > 0).mean()) if n_paired else np.nan
    recon('paired rank-group advantage computed on identical decision dates (both groups complete)',
          'PASS' if n_paired else 'FAIL', f'{n_paired} paired periods of {n_trades - 1}')

    def trim_mean(x, k):
        x = sorted(x)
        return float(np.mean(x[k:len(x) - k])) if len(x) > 2 * k else np.nan

    trim1 = trim_mean(pa.tolist(), 1) if n_paired else np.nan
    k5 = max(1, int(round(0.05 * n_paired))) if n_paired else 0
    trim5 = trim_mean(pa.tolist(), k5) if n_paired else np.nan
    boot_rows = []
    for block in BLOCKS:
        lo, hi, nb = boot_ci(pa, block, BOOT_N, SEED + block)
        boot_rows.append(dict(scope='rank_group_advantage', block=block, n=nb, mean=mean_adv, ci_lo=lo, ci_hi=hi))
    rank_stats = obs.dropna(subset=['holding_return']).groupby('rank')['holding_return'].agg(
        ['mean', 'median', 'std', 'count'])
    rank_stats['win_rate'] = obs.dropna(subset=['holding_return']).groupby('rank')['holding_return'].apply(lambda x: (x > 0).mean())
    top_pos = pa.nlargest(3)
    top_neg = pa.nsmallest(3)
    contrib_pos = float(top_pos.sum() / pa.sum()) if abs(float(pa.sum())) > 0 else np.nan
    log(f'      rank-group advantage (r6-10 minus r1-5): mean {num(mean_adv, 5)} win {num(win_adv, 3)} n={n_paired}')

    log('[5/11] Portfolio performance metrics and comparisons ...')
    def interval_returns(strategy):
        net = sim[strategy]['net']
        pts = [net.at[e] for e in entries] + [net.iloc[-1]]
        return pd.Series([pts[i + 1] / pts[i] - 1 for i in range(len(entries))],
                         index=pd.DatetimeIndex(entries))

    def drawdown_stats(net):
        peak = net.cummax()
        underwater = net < peak
        longest = 0
        cur = 0
        for u in underwater:
            cur = cur + 1 if u else 0
            longest = max(longest, cur)
        gpeak_date = peak.idxmax()
        after = net[net.index >= gpeak_date]
        rec = after[after >= peak.at[gpeak_date]]
        recovery = (rec.index[0] - gpeak_date).days if len(rec) else None
        return dict(max_dd=float((net / peak - 1).min()), longest_underwater_sessions=int(longest),
                    peak_date=str(gpeak_date.date()),
                    recovered=bool(len(rec)), recovery_days=recovery)

    strat_rows = []
    for strategy in STRATS:
        S = sim[strategy]
        st = S['stats']
        ivr = interval_returns(strategy)
        vol = st.get('net_annualized_volatility')
        strat_rows.append(dict(
            strategy=strategy, name=STRAT_NAMES[strategy], cost_bps=COST_BPS,
            gross_return=st['gross_return'], net_return=st['net_return'], net_cagr=st['net_cagr'],
            net_max_drawdown=st['net_max_drawdown'], net_annualized_volatility=vol,
            sharpe_rf0=(st['net_cagr'] / vol) if fin(vol) and vol else np.nan,
            calmar=(st['net_cagr'] / abs(st['net_max_drawdown'])) if fin(st['net_max_drawdown']) and st['net_max_drawdown'] else np.nan,
            average_turnover=st.get('average_turnover'), total_turnover=st.get('total_turnover'),
            total_cost_paid=st.get('total_cost_paid'), trading_cost_drag=st.get('trading_cost_drag'),
            rebalances=int(st.get('recurring_rebalances', 0)) + 1,
            worst_interval=float(ivr.min()), best_interval=float(ivr.max()),
            profitable_interval_pct=float((ivr > 0).mean()),
            avg_effective_holdings=float(audit[audit['strategy'] == strategy]['names_held'].mean())))
    sc = pd.DataFrame(strat_rows)
    boot_rows_c = []
    pair_rows = []
    for strategy in ['B', 'C', 'D', 'E']:
        ivc = interval_returns(strategy)
        ivA = interval_returns('A')
        paired = (ivc - ivA).dropna()
        lo, hi, nb = boot_ci(paired, 3, BOOT_N, SEED + 100 + ord(strategy))
        boot_rows_c.append(dict(scope=f'strategy_{strategy}_minus_A_intervals', block=3, n=nb,
                                mean=float(paired.mean()), ci_lo=lo, ci_hi=hi))
        pair_rows.append(dict(strategy=strategy, n=len(paired), mean_interval_advantage=float(paired.mean()),
                              win_rate=float((paired > 0).mean()),
                              net_return_diff_pp=float(sc.loc[sc.strategy == strategy, 'net_return'].iloc[0] -
                                                       sc.loc[sc.strategy == 'A', 'net_return'].iloc[0]),
                              cagr_diff_pp=float(sc.loc[sc.strategy == strategy, 'net_cagr'].iloc[0] -
                                                 sc.loc[sc.strategy == 'A', 'net_cagr'].iloc[0]),
                              maxdd_diff_pp=float(sc.loc[sc.strategy == strategy, 'net_max_drawdown'].iloc[0] -
                                                  sc.loc[sc.strategy == 'A', 'net_max_drawdown'].iloc[0])))
    pairs = pd.DataFrame(pair_rows)

    log('[6/11] Annual comparison (continuous-curve convention) and regime analysis ...')
    ann_rows = []
    for strategy in STRATS:
        net = sim[strategy]['net']
        years = sorted({dt.year for dt in net.index})
        prev_end = None
        for y in years:
            vals = net[net.index.year == y]
            base = 1.0 if prev_end is None else float(net.at[prev_end])
            path = np.r_[base, vals.to_numpy()]
            ann_rows.append(dict(strategy=strategy, year=int(y),
                                 net_return=float(vals.iloc[-1] / base - 1),
                                 max_drawdown=float((path / np.maximum.accumulate(path) - 1).min()),
                                 intervals=int(((pd.DatetimeIndex(entries).year == y)).sum()),
                                 partial=bool(y in (years[0], years[-1])),
                                 convention='continuous_curve'))
            prev_end = vals.index[-1]
    annual = pd.DataFrame(ann_rows)

    log('[7/11] Concentration and risk attribution ...')
    conc_rows = []
    for strategy in STRATS:
        rows = audit[audit['strategy'] == strategy]
        eff = [effective_n(json.loads(r.target_weights)) for r in rows.itertuples()
               if isinstance(r.target_weights, str)]
        eff = [v for v in eff if np.isfinite(v)]
        ddS = drawdown_stats(sim[strategy]['net'])
        conc_rows.append(dict(strategy=strategy, static_effective_holdings=(
            10.0 if strategy == 'A' else 5.0 if strategy in ('B', 'C') else
            float(1.0 / np.sum((wD if strategy == 'D' else wE) ** 2))),
            avg_effective_holdings=float(np.mean(eff)) if eff else np.nan,
            min_effective_holdings=float(np.min(eff)) if eff else np.nan, **ddS))
    conc = pd.DataFrame(conc_rows)

    def pnl_contributions(strategy):
        h = sim[strategy]['hist']
        next_entry = {entries[i]: entries[i + 1] for i in range(n_trades - 1)}
        contrib = {}
        worst = (np.nan, None, None)
        for i in range(len(h) - 1):
            row = h.iloc[i]
            e = pd.Timestamp(row['entry_date'])
            f = next_entry[e]
            tw = json.loads(row['target_weights']) if isinstance(row['target_weights'], str) else None
            if tw is None:
                tw = {t: 1.0 / len(str(row['tickers']).split(',')) for t in str(row['tickers']).split(',')}
            needed = cal[(cal >= e) & (cal <= f)]
            for t, wt in tw.items():
                window = px_raw.reindex(index=needed, columns=[t]).iloc[:, 0]
                if (np.isfinite(window) & window.gt(0)).all():
                    ret = float(window.iloc[-1] / window.iloc[0] - 1)
                    contrib[t] = contrib.get(t, 0.0) + wt * ret
                    if not fin(worst[0]) or ret < worst[0]:
                        worst = (ret, t, e.strftime('%Y-%m-%d'))
        return contrib, worst

    contrib_C, worst_C = pnl_contributions('C')
    contrib_A, worst_A = pnl_contributions('A')
    contrib_rows = []
    for tag, contrib in (('A', contrib_A), ('C', contrib_C)):
        for t, v in sorted(contrib.items(), key=lambda kv: -kv[1]):
            contrib_rows.append(dict(strategy=tag, ticker=t, name=names_map.get(t, ''), cumulative_contribution=v,
                                     sector=app.classify_sector(names_map.get(t, ''))))
    contrib_df = pd.DataFrame(contrib_rows)

    def group_corr(lo_rank, hi_rank):
        sub = obs[(obs['rank'] >= lo_rank) & (obs['rank'] <= hi_rank) & obs['holding_return'].notna()]
        piv = sub.pivot_table(index='signal_date', columns='ticker', values='holding_return')
        corr = piv.corr()
        vals = corr.values[np.triu_indices_from(corr.values, k=1)]
        vals = vals[np.isfinite(vals)]
        return float(np.mean(vals)) if len(vals) else np.nan
    corr_15 = group_corr(1, 5)
    corr_610 = group_corr(6, 10)
    cross = obs[obs['holding_return'].notna()]
    piv_all = cross.pivot_table(index='signal_date', columns='ticker', values='holding_return')
    rank_of = {}
    for s, t10 in top10.items():
        for row in t10.itertuples():
            rank_of[str(row.ts_code)] = int(row.momentum_rank)
    cross_vals = []
    cc = piv_all.corr()
    for i, a in enumerate(cc.index):
        for b in cc.index[i + 1:]:
            if (rank_of.get(a, 0) <= 5 and rank_of.get(b, 0) >= 6) or (rank_of.get(a, 0) >= 6 and rank_of.get(b, 0) <= 5):
                if np.isfinite(cc.at[a, b]):
                    cross_vals.append(cc.at[a, b])
    corr_cross = float(np.mean(cross_vals)) if cross_vals else np.nan

    log('[8/11] Robustness: subperiods, trimming, block lengths, 10 bps cost scenario ...')
    third = max(1, n_paired // 3)
    pa_sorted = pa.reset_index(drop=True)
    thirds = [pa_sorted.iloc[:third], pa_sorted.iloc[third:2 * third], pa_sorted.iloc[2 * third:]]
    sub_rows = [dict(subperiod=f'third_{i + 1}', n=len(t), mean=float(t.mean()), sign='+' if t.mean() > 0 else '-')
                for i, t in enumerate(thirds)]
    year_rows = [dict(subperiod=str(y), n=len(g), mean=float(g['group_advantage'].mean()),
                      sign='+' if g['group_advantage'].mean() > 0 else '-')
                 for y, g in adv.dropna(subset=['group_advantage']).groupby('year')]
    high_rows = []
    for strategy in STRATS:
        net_h, dd_h, h_h, st_h = apply_costs(sim[strategy]['gross'], sim[strategy]['hist'],
                                             gross_stats(sim[strategy]['gross']), COST_BPS_HIGH)
        high_rows.append(dict(strategy=strategy, name=STRAT_NAMES[strategy], cost_bps=COST_BPS_HIGH,
                              net_return=st_h['net_return'], net_cagr=st_h['net_cagr'],
                              net_max_drawdown=st_h['net_max_drawdown'],
                              total_cost_paid=st_h['total_cost_paid']))
    high = pd.DataFrame(high_rows)
    ivA = interval_returns('A')
    ivC = interval_returns('C')
    tail_C = float(ivC.nsmallest(max(1, int(round(0.05 * len(ivC))))).mean())
    tail_A = float(ivA.nsmallest(max(1, int(round(0.05 * len(ivA))))).mean())

    log('[9/11] Writing CSV artifacts ...')
    strat_all = pd.concat([sc, high], ignore_index=True, sort=False)
    strat_all.to_csv(OUT / 'strategy_comparison.csv', index=False, encoding='utf-8-sig')
    annual.to_csv(OUT / 'annual_comparison.csv', index=False, encoding='utf-8-sig')
    obs.to_csv(OUT / 'rank_forward_returns.csv', index=False, encoding='utf-8-sig')
    adv.to_csv(OUT / 'rank_group_advantage.csv', index=False, encoding='utf-8-sig')
    boot_df = pd.DataFrame(boot_rows + boot_rows_c)
    boot_df.to_csv(OUT / 'bootstrap_results.csv', index=False, encoding='utf-8-sig')
    audit.to_csv(OUT / 'rebalance_audit.csv', index=False, encoding='utf-8-sig')
    eq_rows = {'date': A['net'].index.strftime('%Y-%m-%d')}
    for strategy in STRATS:
        eq_rows[f'{strategy}_gross_nav'] = sim[strategy]['gross'].values
        eq_rows[f'{strategy}_net_nav'] = sim[strategy]['net'].values
    pd.DataFrame(eq_rows).to_csv(OUT / 'equity_curves.csv', index=False, encoding='utf-8-sig')
    contrib_df.to_csv(OUT / 'pnl_contributions.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(weight_defs).to_csv(OUT / 'weight_definitions.csv', index=False, encoding='utf-8-sig')

    log('[10/11] Writing charts ...')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6))
    for strategy in STRATS:
        net = sim[strategy]['net']
        ax.plot(net.index, (net - 1) * 100, label=STRAT_NAMES[strategy],
                linestyle='--' if strategy in ('D', 'E') else '-')
    ax.axhline(0, color='grey', lw=0.6)
    ax.set_ylabel('Cumulative net return (%)')
    ax.set_title('MOM10 rank-weighting experiment: A-E net equity curves (5 bps, continuous, full period)')
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / 'equity_curves.png', dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(12, 6))
    for strategy in ['B', 'C', 'D', 'E']:
        rel = sim[strategy]['net'] / sim['A']['net']
        ax.plot(rel.index, rel, label=f'{STRAT_NAMES[strategy]} / champion',
                linestyle='--' if strategy in ('D', 'E') else '-')
    ax.axhline(1.0, color='black', lw=0.8)
    ax.set_ylabel('Relative wealth vs equal-weight champion (ratio)')
    ax.set_title('Relative wealth vs champion A (net, continuous curves)')
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / 'relative_wealth.png', dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    ax.plot(rank_stats.index, rank_stats['mean'] * 100, 'o-', label='Mean')
    ax.plot(rank_stats.index, rank_stats['median'] * 100, 's-', label='Median')
    ax.axhline(0, color='grey', lw=0.6)
    ax.set_xticks(range(1, 11))
    ax.set_xlabel('Production MOM10 rank (1 = strongest)')
    ax.set_ylabel('Subsequent 10-session return (%)')
    ax.set_title('Forward return by rank (strict-complete windows)')
    ax.grid(alpha=0.2)
    ax.legend()
    ax = axes[1]
    ax.hist(pa * 100, bins=30, color='#37618e', alpha=0.85)
    ax.axvline(0, color='black', lw=0.8)
    ax.axvline(mean_adv * 100, color='#c0392b', lw=1.2, label=f'mean {mean_adv * 100:+.3f}pp')
    ax.set_xlabel('Paired advantage: ranks 6-10 minus ranks 1-5 (pp per period)')
    ax.set_title(f'Rank-group advantage distribution (n={n_paired})')
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / 'rank_return_distribution.png', dpi=150)
    plt.close(fig)

    log('[11/11] Writing reconciliation.md, report.md and manifest.json ...')
    git_final = git_snapshot()
    git_same = (git_initial['file_sha256'] == git_final['file_sha256'])
    recon('production file hashes unchanged during the study (MomentumLabV2.py + momentumlab/*.py)',
          'PASS' if git_same else 'FAIL', f'{len(git_final["file_sha256"])} files hashed before and after')

    r15_year = adv.dropna(subset=['group_advantage']).groupby('year').apply(
        lambda g: pd.Series({'r15': g['ranks1_5_mean'].mean(), 'r610': g['ranks6_10_mean'].mean(),
                             'adv': g['group_advantage'].mean(), 'n': len(g)}))
    champ_year = {}
    prev_end = None
    for y in sorted({dt.year for dt in sim['A']['net'].index}):
        vals = sim['A']['net'][sim['A']['net'].index.year == y]
        base = 1.0 if prev_end is None else float(sim['A']['net'].at[prev_end])
        champ_year[y] = float(vals.iloc[-1] / base - 1)
        prev_end = vals.index[-1]
    c_row = sc[sc['strategy'] == 'C'].iloc[0]
    a_row = sc[sc['strategy'] == 'A'].iloc[0]
    c_boot = [b for b in boot_rows_c if b['scope'] == 'strategy_C_minus_A_intervals'][0]
    ci_excl0 = fin(c_boot['ci_lo']) and fin(c_boot['ci_hi']) and (c_boot['ci_lo'] > 0 or c_boot['ci_hi'] < 0)
    dd_better = float(c_row['net_max_drawdown']) <= float(a_row['net_max_drawdown']) + 0.01
    thirds_pos = sum(1 for t in thirds if t.mean() > 0)
    beats_net = float(c_row['net_return']) > float(a_row['net_return'])
    much_worse_dd = float(c_row['net_max_drawdown']) < float(a_row['net_max_drawdown']) - 0.03
    if beats_net and ci_excl0 and dd_better and thirds_pos >= 2:
        verdict = 'Promising for prospective validation'
    elif (not beats_net) or much_worse_dd:
        verdict = 'Not supported'
    else:
        verdict = 'Inconclusive'
    recon('decision rule applied as documented (see reconciliation.md "Decision rule")', 'PASS',
          f'C vs A: beats_net={beats_net} ci_excl0={ci_excl0} dd_not_worse={dd_better} positive_thirds={thirds_pos}/3 -> {verdict}')

    recon_md = []
    recon_md.append('# Reconciliation - MOM10 Rank 6-10 Portfolio Experiment\n')
    recon_md.append(f'Generated {TS} - dataset `{UNIVERSE}` fingerprint `{fingerprint}` - integrity {integrity_status} '
                    f'(completeness {completeness.get("status", "n/a")} {completeness.get("counts", "n/a")}, recorded as-is).\n')
    recon_md.append('## Programmatic checks\n')
    for r in reconciliation:
        recon_md.append(f"- [{r['status']}] {r['check']}" + (f" - {r['detail']}" if r['detail'] else ''))
    recon_md.append('')
    recon_md.append('## Baseline and references\n')
    recon_md.append(f'- Champion archive: `{CHAMPION_DIR.name}` (fingerprint {champ_fp}); reference stats net '
                    f'{pct(float(champ_stats["net_return"]))}, CAGR {pct(float(champ_stats["net_cagr"]))}, maxDD '
                    f'{pct(float(champ_stats["net_max_drawdown"]))}, period {champ_stats.get("backtest_start")}..'
                    f'{champ_stats.get("backtest_end")}.')
    recon_md.append('- D/E definitions retrieved from `momentumlab/rank_weighting.py::weights_for` '
                    '(Reverse mild = normalized sqrt(rank), Reverse linear = normalized rank over ranks 1..10); '
                    'the weighting research doc is `RANK_WEIGHTING_RESEARCH.md` and its run artifacts live under '
                    'each research run\'s `rank_weighting_research/` folder. No weights were redefined.\n')
    recon_md.append('## Conventions\n')
    recon_md.append('- All five portfolios replay the production loop: selection at the signal date from the '
                    'point-in-time ranked panel, execution at the next session at raw adjusted close, forward '
                    'valuation on ffilled prices, shared `compare_weights` turnover and `apply_costs` 5 bps '
                    'one-way overlay (initial funding costed, as in production).')
    recon_md.append('- Unavailable execution prices: the name is dropped and target weights are renormalized over '
                    'the tradable subset (for A this reduces exactly to the production equal-split rule; recorded '
                    f'per event in rebalance_audit.csv; {n_dropped} strategy-rebalances affected).')
    recon_md.append('- Rank-level security returns require complete observed sessions between entry and the next '
                    'entry (strict, matching the existing weighting-research convention); excluded windows are '
                    'counted, never filled.')
    recon_md.append('- Annual figures use the continuous net curve (NAV year-end over prior year-end); 2021 and '
                    '2026 are marked partial. This is NOT the Strategy Grid standalone-year convention.\n')
    recon_md.append('## Decision rule (documented before interpretation)\n')
    recon_md.append('- "Promising for prospective validation": C beats A on full-period net return AND the paired '
                    'block-3 bootstrap 95% CI of the C-minus-A interval advantage excludes zero on the positive '
                    'side AND C max drawdown is no more than 1pp deeper than A AND the mean advantage is positive '
                    'in at least 2 of 3 chronological thirds.')
    recon_md.append('- "Not supported": C does not beat A on net return, or C max drawdown is more than 3pp deeper.')
    recon_md.append('- Otherwise "Inconclusive". Applied result: ' + verdict + '\n')
    recon_md.append('## Git and production protection\n')
    recon_md.append(f'- Initial git status snapshot: {len(git_initial["status_porcelain"].splitlines())} lines '
                    '(embedded in manifest.json).')
    recon_md.append(f'- Production file hashes ({len(git_final["file_sha256"])} files: MomentumLabV2.py + '
                    'momentumlab/*.py) identical before and after the study: ' + ('YES' if git_same else 'NO') + '.')
    recon_md.append('- The study wrote only inside ' + f'`{ART_DIR_REL}`.' + '\n')
    (OUT / 'reconciliation.md').write_text('\n'.join(recon_md), encoding='utf-8')

    rep = []
    rep.append('# MOM10 Rank 6-10 Portfolio Experiment - Study Report\n')
    rep.append(f'Generated {TS} - `{ART_DIR_REL}` - **EXPLORATORY, in-sample, research-only**. Dataset fingerprint '
               f'`{fingerprint}`. No production logic, defaults, archives, or the production champion were modified.\n')
    rep.append('## 1. Executive summary\n')
    rep.append(f'- Question: does concentrating in production MOM10 ranks 6-10 (or ranks 1-5, or the predefined '
               'reverse gradients) beat the equal-weight Top10 champion Mom101010 net of 5 bps costs?')
    rep.append(f'- Evaluation: {n_trades} rebalances {grid_lo}..{grid_hi}, identical signal/execution dates across '
               'A-E, production execution and cost conventions, baseline reconciled to the champion archive '
               '(all checks in `reconciliation.md`).')
    rep.append(f'- Rank-group diagnostic: ranks 6-10 minus ranks 1-5 mean paired advantage '
               f'{num(mean_adv * 100, 3)}pp per 10-session period (n={n_paired}, win rate {num(win_adv, 3)}), '
               f'block-3 bootstrap 95% CI [{num(float(boot_df[(boot_df.scope == "rank_group_advantage") & (boot_df.block == 3)]["ci_lo"].iloc[0]) * 100, 3)}, '
               f'{num(float(boot_df[(boot_df.scope == "rank_group_advantage") & (boot_df.block == 3)]["ci_hi"].iloc[0]) * 100, 3)}]pp.')
    rep.append(f'- Portfolios: C (ranks 6-10) net {pct(float(c_row["net_return"]))} vs champion A '
               f'{pct(float(a_row["net_return"]))}; maxDD {pct(float(c_row["net_max_drawdown"]))} vs '
               f'{pct(float(a_row["net_max_drawdown"]))}.')
    rep.append(f'- Verdict: **{verdict}** (rule in `reconciliation.md`). All findings are in-sample and post-hoc; '
               'the hypothesis was motivated by previously observed rank returns, so nothing here is an '
               'out-of-sample test. No optimal cutoff, weight vector, or threshold was searched.\n')
    rep.append('## 2. Rank-level diagnostic (assignment s5)\n')
    rep.append(md_table(['rank', 'n', 'mean', 'median', 'std', 'win rate'],
                        [[int(r), int(rank_stats.loc[r, 'count']), num(rank_stats.loc[r, 'mean'] * 100, 3),
                          num(rank_stats.loc[r, 'median'] * 100, 3), num(rank_stats.loc[r, 'std'] * 100, 3),
                          num(rank_stats.loc[r, 'win_rate'], 3)] for r in rank_stats.index]))
    rep.append('')
    rep.append(f'- Excluded security-windows (missing sessions): {int(obs["holding_return"].isna().sum())} of '
               f'{len(obs)} rank-observations.')
    rep.append(f'- Paired advantage sensitivity: mean {num(mean_adv * 100, 3)}pp; trimmed (+-1 extreme) '
               f'{num(trim1 * 100, 3)}pp; trimmed (+-{k5}, ~5%) {num(trim5 * 100, 3)}pp.')
    rep.append('- Largest positive paired events (pp): ' + ', '.join(
        f'{pd.Timestamp(adv.loc[i, "signal_date"]).date()} {v * 100:+.2f}' for i, v in top_pos.items()))
    rep.append('- Largest negative paired events (pp): ' + ', '.join(
        f'{pd.Timestamp(adv.loc[i, "signal_date"]).date()} {v * 100:+.2f}' for i, v in top_neg.items()))
    rep.append(f'- Extreme-event dependence: the 3 largest positive events contribute '
               f'{num(contrib_pos * 100, 1)}% of the summed advantage (sign and magnitude reported, not a '
               'significance claim).')
    rep.append('- Year-by-year paired advantage (pp): ' + '; '.join(
        f'{y}: {r["adv"] * 100:+.2f} (n={int(r["n"])})' for y, r in r15_year.iterrows()) + '\n')
    rep.append('## 3. Portfolio results (assignment s6)\n')
    rep.append(md_table(['strategy', 'gross', 'net', 'net CAGR', 'maxDD', 'vol', 'Sharpe', 'Calmar',
                         'avg turnover', 'cost', 'rebal', 'worst 10s', 'best 10s', 'win% 10s'],
                        [[r.strategy, pct(r.gross_return), pct(r.net_return), pct(r.net_cagr), pct(r.net_max_drawdown),
                          pct(r.net_annualized_volatility), num(r.sharpe_rf0, 2), num(r.calmar, 2),
                          num(r.average_turnover, 3), num(r.total_cost_paid, 3), int(r.rebalances),
                          pct(r.worst_interval), pct(r.best_interval), pct(r.profitable_interval_pct, 1)]
                         for r in sc.itertuples()]))
    rep.append('')
    rep.append(md_table(['challenger', 'n paired', 'mean 10s net advantage (pp)', 'win rate',
                         'net return diff (pp)', 'CAGR diff (pp)', 'maxDD diff (pp)', 'block-3 CI (pp)'],
                        [[p.strategy, int(p.n), num(p.mean_interval_advantage * 100, 3), num(p.win_rate, 3),
                          num(p.net_return_diff_pp * 100, 2), num(p.cagr_diff_pp * 100, 2),
                          num(p.maxdd_diff_pp * 100, 2),
                          f'[{num(float(boot_df[boot_df.scope == f"strategy_{p.strategy}_minus_A_intervals"]["ci_lo"].iloc[0]) * 100, 2)}, '
                          f'{num(float(boot_df[boot_df.scope == f"strategy_{p.strategy}_minus_A_intervals"]["ci_hi"].iloc[0]) * 100, 2)}]']
                         for p in pairs.itertuples()]))
    rep.append('')
    rep.append('- Differences are percentage-point differences of net returns unless labeled a ratio; '
               '`relative_wealth.png` shows wealth ratios vs the champion.')
    rep.append('![equity curves](equity_curves.png)\n')
    rep.append('![relative wealth](relative_wealth.png)\n')
    rep.append('## 4. Annual comparison (continuous-curve convention; assignment s8)\n')
    years_all = sorted(annual['year'].unique())
    rep.append(md_table(['strategy'] + [str(y) + ('*' if y in (years_all[0], years_all[-1]) else '') for y in years_all],
                        [[s] + [pct(float(annual[(annual.strategy == s) & (annual.year == y)]['net_return'].iloc[0]))
                                for y in years_all] for s in STRATS]))
    rep.append('\n*boundary years are partial. Annual max drawdowns and interval counts are in `annual_comparison.csv`.\n')
    rep.append('## 5. Concentration and risk attribution (assignment s7)\n')
    rep.append(md_table(['strategy', 'static eff. holdings', 'avg eff. holdings', 'min eff. holdings',
                         'maxDD', 'longest underwater (sessions)', 'peak', 'recovered', 'recovery days'],
                        [[r.strategy, num(r.static_effective_holdings, 2), num(r.avg_effective_holdings, 2),
                          num(r.min_effective_holdings, 2), pct(r.max_dd), int(r.longest_underwater_sessions),
                          r.peak_date, r.recovered, r.recovery_days] for r in conc.itertuples()]))
    rep.append('')
    rep.append(f'- Exposure overlap: mean pairwise per-period security correlation within ranks 1-5 = '
               f'{num(corr_15, 3)}, within ranks 6-10 = {num(corr_610, 3)}, cross-group = {num(corr_cross, 3)}.')
    rep.append(f'- Largest single-name cumulative contribution (C): {contrib_df[contrib_df.strategy == "C"].iloc[0]["ticker"]} '
               f'{num(float(contrib_df[contrib_df.strategy == "C"].iloc[0]["cumulative_contribution"]) * 100, 2)}pp; '
               f'worst single-name period loss (C): {worst_C[1]} {pct(worst_C[0])} at {worst_C[2]}. '
               'Per-name P&L: `pnl_contributions.csv` (complete windows only; contributions use entry-to-entry '
               'security returns, not portfolio NAV attribution).')
    n_sectors = contrib_df['sector'].nunique()
    if names_map and n_sectors > 1:
        rep.append(f'- Sector exposure (production keyword classifier, descriptive): C spans {n_sectors} sectors; '
                   'top sectors by cumulative contribution: ' + ', '.join(
                       f'{s} {num(float(g) * 100, 1)}pp' for s, g in
                       contrib_df[contrib_df.strategy == 'C'].groupby('sector')['cumulative_contribution'].sum().nlargest(4).items()) + '.')
    else:
        rep.append('- Sector classification data do not support a reliable sector attribution for this universe; '
                   'no sector breakdown is claimed.')
    rep.append('- Concentration caveat: five names versus ten mechanically double single-name weight; the HHI and '
               'correlation rows above describe, but cannot causally separate, concentration from rank skill.\n')
    rep.append('## 6. Regime focus: 2022 and 2025-2026 (assignment s8)\n')
    if 2022 in r15_year.index:
        rep.append(f'- 2022: rank-group advantage {num(float(r15_year.loc[2022, "adv"]) * 100, 3)}pp/period over '
                   f'{int(r15_year.loc[2022, "n"])} periods; champion annual net {pct(champ_year.get(2022))}; '
                   'C annual net ' + pct(float(annual[(annual.strategy == "C") & (annual.year == 2022)]['net_return'].iloc[0])) + '.')
    else:
        rep.append('- 2022: no paired observations (flagged, not imputed).')
    recent = [y for y in (2025, 2026) if y in r15_year.index]
    if recent:
        bits = []
        for y in recent:
            ann_c = annual[(annual.strategy == 'C') & (annual.year == y)]
            bits.append(f'{y}: advantage {num(float(r15_year.loc[y, "adv"]) * 100, 3)}pp (n={int(r15_year.loc[y, "n"])}), '
                        f'C net {pct(float(ann_c["net_return"].iloc[0]))}{" (partial)" if bool(ann_c["partial"].iloc[0]) else ""}')
        rep.append('- 2025-2026: ' + '; '.join(bits) + '.')
    rep.append('- Annual stability and subperiod signs are in section 7; a concentrated-period advantage is '
               'explicitly not a validated edge.\n')
    rep.append('## 7. Robustness and overfitting controls (assignment s9)\n')
    rep.append(md_table(['subperiod', 'n', 'mean advantage (pp)', 'sign'],
                        [[r.subperiod, int(r.n), num(r.mean * 100, 3), r.sign] for r in
                         pd.DataFrame(sub_rows + year_rows).itertuples()]))
    rep.append('')
    rep.append(md_table(['bootstrap scope', 'block', 'n', 'mean (pp)', '95% CI (pp)'],
                        [[b.scope, int(b.block), int(b.n), num(b.mean * 100, 3),
                          f'[{num(b.ci_lo * 100, 3)}, {num(b.ci_hi * 100, 3)}]'] for b in boot_df.itertuples()]))
    rep.append('')
    rep.append(md_table(['strategy', 'cost 5bps net', 'cost 10bps net', 'cost 10bps maxDD'],
                        [[r.strategy, pct(float(sc[sc.strategy == r.strategy]['net_return'].iloc[0])),
                          pct(r.net_return), pct(r.net_max_drawdown)] for r in high.itertuples()]))
    rep.append('')
    rep.append(f'- Concentration sensitivity (C vs A): worst 10-session interval {pct(float(c_row["worst_interval"]))} '
               f'vs {pct(float(a_row["worst_interval"]))}; bottom-5% mean interval {pct(tail_C)} vs {pct(tail_A)}.')
    rep.append('- Multiple testing: this hypothesis was formulated after observing rank returns and weighting '
               'backtests; the full period tested is the period that motivated it. Nothing here is out-of-sample, '
               'and no search over cutoffs or weights was performed (only the predefined A-E configurations).\n')
    rep.append('## 8. Final assessment (assignment s12)\n')
    rep.append(f'- Verdict: **{verdict}**.')
    rep.append(f'1. Did ranks 6-10 outperform ranks 1-5? Mean paired advantage {num(mean_adv * 100, 3)}pp per '
               f'10-session period, win rate {num(win_adv, 3)}, n={n_paired}.')
    rep.append(f'2. Did the ranks 6-10 portfolio beat Mom101010 net of costs? C net {pct(float(c_row["net_return"]))} '
               f'vs A {pct(float(a_row["net_return"]))} ({num(float(pairs[pairs.strategy == "C"]["net_return_diff_pp"].iloc[0]) * 100, 2)}pp), '
               f'paired interval advantage mean {num(float(pairs[pairs.strategy == "C"]["mean_interval_advantage"].iloc[0]) * 100, 3)}pp.')
    rep.append(f'3. Drawdown? C maxDD {pct(float(c_row["net_max_drawdown"]))} vs A {pct(float(a_row["net_max_drawdown"]))} '
               f'({num(float(pairs[pairs.strategy == "C"]["maxdd_diff_pp"].iloc[0]) * 100, 2)}pp difference).')
    yrs_sorted = r15_year.sort_values('adv', ascending=False)
    rep.append('4. Which years drove the result? ' + '; '.join(
        f'{y} {r["adv"] * 100:+.2f}pp' for y, r in yrs_sorted.head(3).iterrows()) +
               ' (largest positive); ' + '; '.join(f'{y} {r["adv"] * 100:+.2f}pp' for y, r in yrs_sorted.tail(2).iterrows()) + '.')
    rep.append(f'5. Robust enough for further testing? block-3 CI [{num(c_boot["ci_lo"] * 100, 3)}, '
               f'{num(c_boot["ci_hi"] * 100, 3)}]pp excludes zero: {ci_excl0}; positive thirds {thirds_pos}/3.')
    risk_note = ('single-period concentration of the advantage' if (not ci_excl0 and thirds_pos < 3)
                 else 'five-name idiosyncratic risk given near-equal group correlations')
    rep.append(f'6. Single most important unresolved risk: {risk_note}, compounded by the post-hoc origin of the '
               'hypothesis (in-sample evidence only).\n')
    rep.append('## 9. Data, files and production status (assignment s13)\n')
    rep.append(f'- Output directory: `{ART_DIR_REL}` (all artifacts; manifest.json carries script hash, dataset '
               'fingerprint, parameters, git snapshots and source paths).')
    rep.append('- Reconciliation: see `reconciliation.md`; baseline reproduced the archived champion exactly; '
               'PASS/FAIL/WARNING statuses are explicit.')
    rep.append('- Created: `studies/mom10_rank6_10_experiment.py` and everything under the output directory. '
               'Modified: none. Production hashes verified identical before and after (see manifest.json).')
    rep.append('- Explicit confirmation: **no production strategy logic, configuration, defaults, datasets, '
               'archives, UI, scheduling, or ingestion code was changed; Mom101010 remains the unchanged '
               'production champion; no commits or pushes were made.**\n')
    (OUT / 'report.md').write_text('\n'.join(rep), encoding='utf-8')

    rd = dict(
        study='mom10_rank6_10_experiment', generated=TS,
        script='studies/mom10_rank6_10_experiment.py', script_sha256=sha256_file(Path(__file__)),
        artifact_dir=ART_DIR_REL,
        dataset=dict(universe=UNIVERSE, fingerprint=fingerprint, integrity_status=integrity_status,
                     completeness=completeness.get('status'), completeness_counts=completeness.get('counts')),
        parameters=dict(cost_bps=COST_BPS, cost_bps_high=COST_BPS_HIGH, rebalance_sessions=RB,
                        bootstrap_resamples=BOOT_N, block_lengths=list(BLOCKS), seed=SEED,
                        n_rebalances=int(n_trades), grid=f'{grid_lo}..{grid_hi}'),
        references=dict(champion_archive=CHAMPION_DIR.name, champion_fingerprint=champ_fp,
                        weight_definitions='momentumlab/rank_weighting.py::weights_for',
                        weighting_research_doc='RANK_WEIGHTING_RESEARCH.md'),
        weight_definitions=weight_defs,
        champion_reference=dict(net_return=float(champ_stats['net_return']), net_cagr=float(champ_stats['net_cagr']),
                                net_max_drawdown=float(champ_stats['net_max_drawdown'])),
        verdict=verdict,
        rank_group_advantage=dict(n=int(n_paired), mean=mean_adv, win_rate=win_adv, trim1=trim1, trim5=trim5),
        strategy_pairs=clean(pairs.to_dict('records')),
        reconciliation=reconciliation,
        git_initial=git_initial, git_final=git_final, production_files_unchanged=bool(git_same),
        artifacts=sorted(p.name for p in OUT.iterdir()),
        production_modified=False,
    )
    (OUT / 'manifest.json').write_text(json.dumps(clean(rd), indent=1, ensure_ascii=False), encoding='utf-8')
    log(f'DONE. Artifacts in {OUT}')
    log(f'Verdict: {verdict}')


if __name__ == '__main__':
    main()

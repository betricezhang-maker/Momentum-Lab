"""Hypothetical cash overlay using the selected production MOM scores."""
import numpy as np
import pandas as pd
from .cash_warning import economic_path
from .serialization import sanitize_for_json
from .dates import normalize_dates


def save_comparison_chart(curve,result,path):
    """Plot existing diagnostic curves without recalculating either return path."""
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(12,5))
    try:
        ax.plot(curve.index,(curve.baseline-1)*100,label='Original strategy · matching completed periods',color='#286cb0')
        ax.plot(curve.index,(curve.cash_warning-1)*100,label='With participation control',color='#d18a00')
        ax.set_ylabel('Cumulative net return (%)');ax.set_xlabel('Valuation date');ax.axhline(0,color='grey',linewidth=.6);ax.grid(alpha=.2);ax.legend()
        coverage='Off' if result['coverage']==0 else f"{result['coverage']:.0%}"
        delta=result['stats']['return_difference']*100
        ax.set_title(f"MOM{result['lookback']} · {result['selection']} · {result['rebalance_days']}D · cash when participation ≤ {result['threshold']:.0%}\nMinimum coverage: {coverage} · final benefit / penalty: {delta:+.2f} percentage points")
        fig.text(.5,.01,'Hypothetical, retrospective · cash earns 0% · modeled costs plus conservative switching charges · unfinished final period excluded',ha='center',fontsize=8)
        fig.tight_layout(rect=(0,.04,1,1));fig.savefig(path,dpi=130)
    finally:plt.close(fig)


def compare(ranked,weights,history,equity,threshold,coverage=.8,cost=5):
    threshold=float(threshold)
    if not np.isfinite(threshold) or not 0<=threshold<=1 or not np.isclose(threshold*10,round(threshold*10)):
        raise ValueError('Choose a participation threshold from 0% to 100% in 10% steps')
    if not np.isfinite(coverage) or not 0<=coverage<=1:raise ValueError('Invalid minimum coverage')
    h=history.copy();h['entry_date']=pd.to_datetime(h.entry_date);h['signal_date']=pd.to_datetime(h.signal_date)
    h=h.sort_values('entry_date').reset_index(drop=True)
    if len(h)<2:raise ValueError('Two executions are required for a completed holding period')
    if h.entry_date.duplicated().any() or not (h.signal_date<h.entry_date).all():raise ValueError('Invalid execution records')
    w=weights.copy();w['trade_date']=normalize_dates(w.trade_date)
    memberships={d:set(g.con_code.astype(str)) for d,g in w.groupby('trade_date')}
    scores={pd.Timestamp(d):g.set_index('ts_code') for d,g in ranked.groupby('trade_date')};rows=[]
    for i in range(len(h)-1):
        row=h.iloc[i];past=[d for d in memberships if d<=row.signal_date]
        members=sorted(memberships[max(past)]) if past else []
        g=scores.get(row.signal_date,pd.DataFrame(columns=['momentum_score'])).reindex(members)
        v=pd.to_numeric(g.momentum_score,errors='coerce');valid=np.isfinite(v)
        if 'signal_window_valid' in g:valid &= g.signal_window_valid.fillna(False).eq(True)
        if 'csi300_member' in g:valid &= g.csi300_member.eq(1)
        n=int(valid.sum());cov=n/len(members) if members else 0
        p=float(v[valid].gt(0).mean()) if n and cov>=coverage else np.nan
        rows.append(dict(signal_date=row.signal_date,execution_date=row.entry_date,holding_end_date=h.iloc[i+1].entry_date,
            eligible_count=len(members),valid_count=n,excluded_count=len(members)-n,coverage=cov,participation=p,
            cash=bool(np.isfinite(p) and p<=threshold)))
    d=pd.DataFrame(rows);nav=equity.copy();nav.index=pd.to_datetime(nav.index)
    if nav.index.duplicated().any() or not nav.index.is_monotonic_increasing or not (np.isfinite(nav)&nav.gt(0)).all():raise ValueError('Invalid saved NAV')
    if not set(h.entry_date).issubset(nav.index):raise ValueError('Missing execution boundary')
    if not (np.isfinite(h.nav_before)&h.nav_before.gt(0)).all():raise ValueError('Invalid execution NAV')
    rate=float(cost)/20000
    if not np.isfinite(rate) or not 0<=rate<1:raise ValueError('Invalid switching cost')
    curve,stats=economic_path(d,d.cash,nav,h.set_index('entry_date'),rate)
    stats.update(periods=len(d),cash_periods=int(d.cash.sum()),fallback_periods=int(d.participation.isna().sum()),start=str(curve.index.min().date()),end=str(curve.index.max().date()))
    return sanitize_for_json(dict(status='available',threshold=threshold,coverage=coverage,stats=stats)),curve,d

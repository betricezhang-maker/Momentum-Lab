"""V4 costs: basis points per unit of securities-only one-way turnover."""
import math
import numpy as np
import pandas as pd
from .turnover import summarize_turnover


def validate_cost(value):
    rate = float(value)
    if not math.isfinite(rate) or not 0 <= rate <= 10000:
        raise ValueError('Trading cost must be finite and between 0 and 10000 bps.')
    return rate / 10000


def apply_costs(gross, history, gross_stats, cost_bps):
    rate = validate_cost(cost_bps)
    factors = pd.Series(1.0, index=gross.index)
    records = history.to_dict('records')
    multiplier = 1.0
    for row in records:
        date = pd.Timestamp(row['entry_date'])
        drag = float(row['one_way_turnover']) * rate
        before = float(gross.loc[date]) * multiplier
        row.update(cost_rate=rate, cost_drag=drag, cost=before*drag, nav_before=before,
                   nav_after=before*(1-drag), gross_nav=float(gross.loc[date]))
        factors.loc[date] *= 1-drag
        multiplier *= 1-drag
    net = gross * factors.cumprod()
    dd = net / net.cummax().clip(lower=1.0) - 1
    returns = net.pct_change(fill_method=None).fillna(0)
    returns.iloc[0] = net.iloc[0]-1
    cagr = float(net.iloc[-1] ** (252/max(len(net)-1,1))-1)
    vol = float(returns.std(ddof=1)*np.sqrt(252))
    stats = dict(gross_stats)
    stats.update(summarize_turnover(records, len(gross)))
    stats.update(gross_return=gross_stats['total_return'], net_return=float(net.iloc[-1]-1),
                 gross_cagr=gross_stats['annualized_return'], net_cagr=cagr,
                 gross_max_drawdown=gross_stats['max_drawdown'], net_max_drawdown=float(dd.min()),
                 net_annualized_volatility=vol if math.isfinite(vol) else None,
                 trading_cost_drag=float(gross.iloc[-1]-net.iloc[-1]),
                 total_cost_paid=sum(r['cost'] for r in records), cost_bps=float(cost_bps),
                 cost_convention='one_way_turnover', initial_cost_included=True)
    return net.rename('net_nav'), dd.rename('net_drawdown'), pd.DataFrame(records), stats

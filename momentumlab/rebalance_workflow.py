"""Derived rebalance actions and statuses; no signal or pricing calculations."""
from .turnover import compare_weights


FINAL_STATUSES={'COMPLETED','SKIPPED'}


def recommendations(positions, target, names, nav, ranks=None, scores=None, tolerance=.0025, allocation_nav=None):
    current={r['ticker']:(r.get('market_value',0)/nav if nav else 0) for r in positions}
    quantities={r['ticker']:r.get('quantity',0) for r in positions}
    prices={r['ticker']:r.get('current_price') for r in positions}
    values={r['ticker']:r.get('market_value',0) for r in positions}
    rows=[]
    for ticker in sorted(current.keys()|target.keys(),key=lambda t:(ranks or {}).get(t,10**9)):
        if not quantities.get(ticker,0) and not target.get(ticker,0): continue
        before=current.get(ticker,0);after=target.get(ticker,0);change=after-before
        if before and not after: action,status,side='SELL OUT','EXIT','SELL'
        elif after and not before: action,status,side='NEW BUY','NEW','BUY'
        elif abs(change)<=tolerance: action,status,side='CARRY / NO MATERIAL CHANGE','CARRIED',None
        elif change>0: action,status,side='CARRY / INCREASE','CARRIED','BUY'
        else: action,status,side='CARRY / REDUCE','CARRIED','SELL'
        current_value=values.get(ticker,0);target_value=after*nav
        final_value=after*(nav if allocation_nav is None else allocation_nav)
        trade_value=final_value-current_value
        side='BUY' if trade_value>1e-7 else 'SELL' if trade_value<-1e-7 else None
        price=prices.get(ticker)
        rows.append(dict(action=action,ticker=ticker,name=names.get(ticker,ticker),rank=(ranks or {}).get(ticker),
                         score=(scores or {}).get(ticker),current_quantity=quantities.get(ticker,0),
                         reference_price=price,current_value=current_value,target_value=target_value,
                         current_weight=before,target_weight=after,tracking_difference=before-after,
                         suggested_trade_value=trade_value,
                         suggested_quantity_change=(trade_value/price if price else None),
                         final_quantity=(final_value/price if price else None),final_target_value=final_value,
                         change_vs_previous=final_value-current_value,status=status,trade_side=side))
    return rows,compare_weights(current,target,initial=not any(quantities.values()))


def workflow_status(signal, records, trades, asof, automatically_applied=False):
    if signal is None: return 'UPCOMING'
    related=[r['payload'] for r in records if r['payload'].get('signal_id')==signal['id']]
    if related and 'status' not in related[-1]: return 'COMPLETED'
    if related and related[-1].get('status') in FINAL_STATUSES|{'PARTIALLY_EXECUTED'}:
        return related[-1]['status']
    if automatically_applied: return 'COMPLETED'
    execution=signal['payload'].get('execution_date')
    if not execution or execution>asof: return 'SIGNAL_READY'
    if any(t['side'] in {'BUY','SELL'} and t['date']>=execution for t in trades): return 'PARTIALLY_EXECUTED'
    return 'WAITING_FOR_EXECUTION'

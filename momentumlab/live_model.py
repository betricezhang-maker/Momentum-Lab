"""Dated model targets. Stored targets are immutable; late generation is labeled."""
from bisect import bisect_right
from datetime import date
import pandas as pd
from .data_integrity import validate_signal_context


def select_target_rows(ranked, selection, signal_date):
    """Canonical target selection for one exact signal date."""
    rows=ranked[(ranked['trade_date']==pd.Timestamp(signal_date)) &
                (ranked['csi300_member']==1) & ranked['momentum_rank'].notna()]
    if selection.startswith('Top '):
        return rows[rows['momentum_rank']<=int(selection.split()[1])].sort_values('momentum_rank')
    else:
        return rows[rows['quintile']==int(selection[-1])]


def scheduled_signals(start, calendar, interval, through):
    if not calendar: return [start] if start<=through else []
    result=[start] if start<=through else []
    index=bisect_right(calendar,start)-1+interval
    while index<len(calendar):
        day=calendar[index]
        if day>through:
            break
        if day>start: result.append(day)
        index+=interval
    return result


def next_schedule(start, calendar, interval, asof):
    future=scheduled_signals(start,calendar,interval,calendar[-1]) if calendar else []
    signal=next((d for d in future if d>=asof or bisect_right(calendar,d)<len(calendar) and calendar[bisect_right(calendar,d)]>asof),None)
    at=bisect_right(calendar,signal) if signal else len(calendar)
    return {'next_signal':signal,'next_rebalance':calendar[at] if at<len(calendar) else None,
            'calendar_end':calendar[-1] if calendar else None}


def target_snapshot(strategy, market, signal_date):
    ranked=market['ranked']
    # Roll closed requested dates to the preceding OPEN session, never to an
    # older successful ranking when the current session has incomplete inputs.
    sessions=[d for d in market['calendar'] if pd.Timestamp(d)<=pd.Timestamp(signal_date)]
    if not sessions: raise ValueError('No exchange session on/before the tracking signal date.')
    exact=pd.Timestamp(sessions[-1])
    available=ranked[(ranked['trade_date']==exact) & (ranked['csi300_member']==1) & ranked['momentum_rank'].notna()]
    if available.empty:
        raise ValueError('No eligible momentum rankings on the exact signal date; incomplete/stale windows cannot use an older target.')
    ranking_date=available['trade_date'].max()
    rows=select_target_rows(ranked,strategy['selection'],ranking_date)
    if rows.empty:
        raise ValueError('The selected portfolio has no eligible securities on the signal date.')
    weights={str(t):1/len(rows) for t in rows['ts_code']}
    canonical_signal=ranking_date.strftime('%Y-%m-%d')
    idx=bisect_right(market['calendar'],canonical_signal)
    if 'weights' in market:
        validate_signal_context(market['calendar'],market['weights'],canonical_signal,
                                market['calendar'][idx] if idx<len(market['calendar']) else None,tickers=weights)
    return {'signal_date':canonical_signal,'ranking_date':canonical_signal,
            'provenance':{**market.get('provenance',{}),'signal_date':canonical_signal,
                          'execution_date':market['calendar'][idx] if idx<len(market['calendar']) else None},
            'execution_date':market['calendar'][idx] if idx<len(market['calendar']) else None,
            'weights':weights,'ranks':{str(r.ts_code):int(r.momentum_rank) for r in rows.itertuples()},
            'scores':({str(r.ts_code):(float(r.momentum_score) if pd.notna(r.momentum_score) else None) for r in rows.itertuples()}
                      if 'momentum_score' in rows else {}),
            'names':{t:market.get('names',{}).get(t,t) for t in weights}, 'backfilled':signal_date<date.today().isoformat(),
            'data_end':market['data_end']}

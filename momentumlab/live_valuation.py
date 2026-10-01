"""Manual long-only ledger, valued with raw closes; research model uses adjusted returns."""
from bisect import bisect_right
from datetime import date
import math
import numpy as np
import pandas as pd
from .dates import normalize_dates
from .turnover import compare_weights


def active_trades(records):
    replaced = {r['supersedes'] for r in records if r.get('supersedes')}
    return sorted([{**r['payload'], 'id':r['id'], 'recorded_at':r['recorded_at']} for r in records if r['id'] not in replaced],
                  key=lambda r:(r['date'],r.get('sequence',r['recorded_at']),r['id']))


def active_cash_flows(records):
    return sorted([{**r['payload'], 'id':r['id'], 'recorded_at':r['recorded_at']} for r in records],
                  key=lambda r:(r['date'],r['recorded_at'],r['id']))


def flow_amount(flow):
    amount=float(flow['amount'])
    return amount if flow['kind']=='CONTRIBUTION' else -amount


def ledger(capital, trades, through=None, cash_flows=None):
    cash, positions, fees, realized = float(capital), {}, 0., 0.
    contributions=withdrawals=0.
    operations=[]
    for flow in cash_flows or []:
        # Contributions fund same-day buys; withdrawals settle after same-day sales.
        priority=0 if flow['kind']=='CONTRIBUTION' else 2
        operations.append((flow['date'],priority,flow.get('recorded_at',''),flow.get('id',''),'FLOW',flow))
    for trade in trades:
        operations.append((trade['date'],1,trade.get('sequence',trade.get('recorded_at','')),trade.get('id',''),'TRADE',trade))
    for operation_date,_,__,___,kind,item in sorted(operations):
        if through and operation_date > through:
            continue
        if kind=='FLOW':
            amount=float(item['amount'])
            if item['kind']=='CONTRIBUTION': cash+=amount;contributions+=amount
            elif item['kind']=='WITHDRAWAL': cash-=amount;withdrawals+=amount
            else: raise ValueError('Capital flow must be a contribution or withdrawal.')
            if cash < -1e-7: raise ValueError(f"Insufficient cash for withdrawal on {operation_date}.")
            continue
        t=item
        qty, price, fee = t['quantity'], t['price'], t['fee']
        p = positions.setdefault(t['ticker'], {'quantity':0.,'basis':0.})
        if t['side'] in {'BUY','OPENING_TRANSFER'}:
            cash -= qty*price+fee
            p['quantity'] += qty
            p['basis'] += float(t.get('basis',qty*price+fee))
        elif t['side']=='SELL':
            if qty > p['quantity']+1e-9:
                raise ValueError(f"Cannot sell more {t['ticker']} than held on {t['date']}.")
            basis = p['basis']*qty/p['quantity']
            p['quantity'] -= qty
            p['basis'] -= basis
            cash += qty*price-fee
            realized += qty*price-fee-basis
        elif t['side']=='DIVIDEND':
            cash += price-fee
        elif t['side']=='SPLIT':
            if p['quantity'] <= 0:
                raise ValueError('A split requires an existing holding.')
            p['quantity'] *= qty
            cash -= fee
        fees += fee
        if cash < -1e-7:
            raise ValueError(f"Insufficient cash on {t['date']}; borrowing is not supported.")
    positions = {k:{**v,'average_cost':v['basis']/v['quantity']} for k,v in positions.items() if v['quantity']>1e-9}
    return {'cash':max(cash,0.),'positions':positions,'fees':fees,'realized_pl':realized,
            'contributions':contributions,'withdrawals':withdrawals}


class Quotes:
    def __init__(self, prices, column='close'):
        prices=prices.copy()
        prices['trade_date']=normalize_dates(prices['trade_date'])
        self.rows={}
        for ticker, group in prices.groupby('ts_code'):
            group=group.sort_values('trade_date').dropna(subset=[column])
            group=group[pd.to_numeric(group[column],errors='coerce')>0]
            self.rows[str(ticker)]=(list(group['trade_date'].dt.strftime('%Y-%m-%d')),list(group[column].astype(float)))

    def get(self, ticker, day):
        days,values=self.rows.get(ticker,([],[]))
        at=bisect_right(days,day)-1
        return (values[at],days[at]) if at>=0 else (None,None)


def mark(book, quotes, day):
    rows=[]
    for ticker,p in book['positions'].items():
        quote,quote_date=quotes.get(ticker,day)
        # Explicit cost-basis fallback is visible; it is never passed off as a market price.
        price=p['average_cost'] if quote is None else quote
        value=p['quantity']*price
        rows.append(dict(ticker=ticker,**p,current_price=price,price_date=quote_date,
                         valuation_source='cost basis (no market quote)' if quote is None else 'raw close',
                         market_value=value,pl=value-p['basis']))
    nav=book['cash']+sum(r['market_value'] for r in rows)
    for r in rows:
        r['actual_weight']=r['market_value']/nav if nav else 0
    return nav,rows


def performance(values, flows=None):
    if not values:
        return dict(cumulative_return=0,annualized_return=None,annual_volatility=None,max_drawdown=0)
    s=pd.Series(values,dtype=float)
    external=pd.Series(flows if flows is not None else [0.]*len(s),dtype=float)
    if len(external)!=len(s): raise ValueError('NAV values and external cash flows must have equal length.')
    ret=((s-external)/s.shift(1)-1).replace([np.inf,-np.inf],np.nan).iloc[1:].dropna()
    growth=pd.concat([pd.Series([1.]),(1+ret).cumprod()],ignore_index=True)
    cumulative=float(growth.iloc[-1]-1)
    return dict(cumulative_return=cumulative,
                annualized_return=float((1+cumulative)**(252/(len(s)-1))-1) if len(s)>1 and 1+cumulative>=0 else None,
                annual_volatility=float(ret.std(ddof=1)*np.sqrt(252)) if len(ret)>1 else None,
                max_drawdown=float((growth/growth.cummax()-1).min()))


def build_nav(portfolio, trades, signals, market, asof, include_details=False, cash_flows=None):
    start=portfolio['start_date']; capital=portfolio['capital']
    cash_flows=cash_flows or []
    dates=sorted({start,asof}|{d for d in market['calendar'] if start<=d<=asof}|{t['date'] for t in trades if start<=t['date']<=asof}|{f['date'] for f in cash_flows if start<=f['date']<=asof})
    raw=Quotes(market['raw']); adjusted=Quotes(market['adjusted'],'adj_close')
    pending={}
    for s in signals:
        payload={**s['payload'],'record_id':s.get('id')}
        execution=payload.get('execution_date')
        if not execution:
            at=bisect_right(market['calendar'],payload['signal_date'])
            execution=market['calendar'][at] if at<len(market['calendar']) else None
        if execution: pending.setdefault(execution,[]).append(payload)
    opening=portfolio.get('strategy',{}).get('opening_model_state') or {}
    model_cash=float(opening.get('cash',capital));model_shares={str(t):float(q) for t,q in opening.get('quantities',{}).items()}
    basis={str(t):float(v) for t,v in opening.get('average_costs',{}).items()};nav=[];journals=[];timeline=[]
    for day in dates:
        actual = None
        if portfolio.get('mode')!='PAPER':
            actual, _=mark(ledger(capital,trades,day,cash_flows),raw,day)
        daily_flows=[f for f in cash_flows if f['date']==day]
        execution_ids={s.get('record_id') for s in pending.get(day,[])}
        model_cash+=sum(flow_amount(f) for f in daily_flows if not f.get('signal_id') or f['signal_id'] not in execution_ids)
        model=model_cash+sum(q*adjusted.get(t,day)[0] for t,q in model_shares.items())
        unfilled=[]
        for signal in pending.get(day,[]):
            adjustment=sum(flow_amount(f) for f in daily_flows if f.get('signal_id') and f['signal_id']==signal.get('record_id'))
            model_cash+=adjustment;model+=adjustment
            eligible={t:w for t,w in signal['weights'].items() if adjusted.get(t,day)[1]==day}
            unfilled=sorted(signal['weights'].keys()-eligible.keys())
            if eligible:
                total_weight=sum(eligible.values())
                eligible={t:w/total_weight for t,w in eligible.items()}
                old={t:q*adjusted.get(t,day)[0]/model for t,q in model_shares.items()} if model else {}
                comparison=compare_weights(old,eligible,initial=not journals)
                turnover=comparison['one_way_turnover']
                before=model
                model *= 1-turnover*portfolio['strategy']['cost_bps']/10000
                old_shares=model_shares
                model_shares={t:model*w/adjusted.get(t,day)[0] for t,w in eligible.items()}
                basis={t:((min(old_shares.get(t,0),q)*basis.get(t,adjusted.get(t,day)[0])+
                           max(q-old_shares.get(t,0),0)*adjusted.get(t,day)[0])/q) for t,q in model_shares.items() if q>0}
                model_cash=model-sum(model*w for w in eligible.values())
                journals.append(dict(date=day,signal_date=signal['signal_date'],**comparison,
                                     cost=before-model,nav_before=before,nav_after=model,
                                     holdings_before=list(old),holdings_after=list(eligible)))
                for ticker in sorted(old.keys()|eligible.keys()):
                    price=adjusted.get(ticker,day)[0]
                    timeline.append(dict(date=day,ticker=ticker,name=market.get('names',{}).get(ticker,ticker),
                                         action='KEEP' if ticker in old and ticker in eligible else 'SELL' if ticker in old else 'BUY',
                                         target_weight=eligible.get(ticker,0),weight=eligible.get(ticker,0),
                                         quantity=model_shares.get(ticker,0),price=price,
                                         position_value=model_shares.get(ticker,0)*(price or 0),source='MODEL'))
        nav.append(dict(date=day,actual_nav=actual,model_nav=model,model_unfilled=','.join(unfilled)))
    if include_details:
        positions=[]
        for ticker,quantity in model_shares.items():
            price,quoted=adjusted.get(ticker,asof)
            value=quantity*price
            positions.append(dict(ticker=ticker,quantity=quantity,current_price=price,price_date=quoted,
                                  market_value=value,actual_weight=value/model if model else 0,
                                  average_cost=basis.get(ticker,price),basis=quantity*basis.get(ticker,price),pl=quantity*(price-basis.get(ticker,price)),
                                  valuation_source='adjusted close (model units)'))
        return nav,raw,dict(journals=journals,timeline=timeline,positions=positions,cash=model_cash)
    return nav,raw

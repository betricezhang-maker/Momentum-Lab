# Exact backtest function from commit b662b15:MomentumLabV2.py.
# Executed in the regression test namespace; no independent engine.
def backtest(ranked,selection,rebalance_days,start_date=None,end_date=None,cost_bps=0):
    d=ranked.sort_values(["trade_date","ts_code"]).copy()
    cal=pd.DatetimeIndex(sorted(d["trade_date"].dropna().unique()))
    px_raw=d.pivot_table(index="trade_date",columns="ts_code",values="adj_close",aggfunc="last").sort_index()
    px_val=px_raw.ffill()

    valid_dates=d.loc[(d["csi300_member"]==1)&d["momentum_rank"].notna(),"trade_date"]
    if valid_dates.empty:
        raise ValueError("No valid ranking dates. Check historical membership / eligibility coverage.")
    first=valid_dates.min()
    last=valid_dates.max()
    if start_date: first=max(first,pd.Timestamp(start_date))
    if end_date: last=min(last,pd.Timestamp(end_date))
    candidates=cal[(cal>=first)&(cal<=last)]
    if len(candidates)<2:
        raise ValueError("Backtest range is too short.")
    first_idx=cal.get_loc(candidates[0])
    sig_idx=[i for i in range(first_idx,len(cal)-1,int(rebalance_days)) if cal[i]<=last]
    sig_to_entry={cal[i+1]:cal[i] for i in sig_idx}

    cash=1.0
    shares={}
    nav=[]
    logs=[]

    def value(date):
        v=cash
        for t,sh in shares.items():
            if t in px_val.columns:
                p=px_val.at[date,t]
                if pd.notna(p): v+=sh*p
        return float(v)

    for di in range(sig_idx[0]+1,len(cal)):
        date=cal[di]
        if end_date and date>pd.Timestamp(end_date):
            break
        before=value(date)
        if date in sig_to_entry:
            sig=sig_to_entry[date]
            today=d[(d["trade_date"]==sig)&(d["csi300_member"]==1)&d["momentum_rank"].notna()].copy()
            chosen=today[select_mask(today,selection)].copy()
            if selection.startswith("Top "): chosen=chosen.sort_values("momentum_rank")
            tickers=[t for t in chosen["ts_code"] if t in px_raw.columns and pd.notna(px_raw.at[date,t])]
            if tickers:
                current={}
                for t,sh in shares.items():
                    p=px_val.at[date,t] if t in px_val.columns else np.nan
                    current[t]=sh*p if pd.notna(p) else 0.0
                target_each=before/len(tickers)
                traded=sum(abs((target_each if t in tickers else 0)-current.get(t,0)) for t in set(current)|set(tickers))
                cost=traded*(float(cost_bps)/10000.0)
                after=max(before-cost,0)
                each=after/len(tickers)
                new={}
                for t in tickers:
                    p=px_raw.at[date,t]
                    if p>0: new[t]=each/p
                shares=new
                cash=after-sum(new[t]*px_raw.at[date,t] for t in new)
                logs.append({"signal_date":sig.strftime("%Y-%m-%d"),"entry_date":date.strftime("%Y-%m-%d"),
                             "names":len(tickers),"one_way_turnover":0.5*traded/before if before>0 else np.nan,
                             "cost":cost,"tickers":",".join(tickers)})
        nav.append((date,value(date)))
        if end_date and date>=pd.Timestamp(end_date):
            break

    s=pd.Series([v for _,v in nav],index=pd.DatetimeIndex([d for d,_ in nav]),name="nav")
    eq=s/s.iloc[0]
    rets=s.pct_change(fill_method=None).fillna(0)
    dd=eq/eq.cummax()-1
    ann_ret=eq.iloc[-1]**(252/max(len(eq)-1,1))-1
    ann_vol=rets.std(ddof=1)*np.sqrt(252)
    stats={
        "total_return":float(eq.iloc[-1]-1),
        "annualized_return":float(ann_ret),
        "annualized_volatility":float(ann_vol),
        "sharpe_rf0":float(ann_ret/ann_vol) if ann_vol>0 else None,
        "max_drawdown":float(dd.min()),
        "rebalances":len(logs),
        "backtest_start":eq.index.min().strftime("%Y-%m-%d"),
        "backtest_end":eq.index.max().strftime("%Y-%m-%d"),
        "observations":int(len(eq))
    }
    return eq,dd,pd.DataFrame(logs),stats

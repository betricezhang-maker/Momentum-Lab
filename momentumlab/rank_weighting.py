"""Fixed Top10 rank-sizing experiments. Never writes production holdings or prices."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .costs import apply_costs
from .turnover import compare_weights
from .live_model import select_target_rows
from .serialization import dumps, sanitize_for_json
from .completeness import open_sessions

METHODS={'Equal weight':(0.,False),'Mild gradient':(.5,False),'Linear gradient':(1.,False),
         'Reverse mild gradient':(.5,True),'Reverse linear gradient':(1.,True)}


def weights_for(alpha, reverse=False):
    if alpha not in (0.,.5,1.):raise ValueError('Only predefined weighting methods are supported')
    w=np.arange(10,0,-1,dtype=float)**alpha
    w=w/w.sum()
    return w[::-1].copy() if reverse else w


def gross_stats(s):
    return dict(total_return=float(s.iloc[-1]-1),annualized_return=float(s.iloc[-1]**(252/max(len(s)-1,1))-1),
                max_drawdown=float((s/s.cummax()-1).min()))


def replay(px,dates,targets,alpha,cost,reverse=False):
    """Production gross-share valuation plus the shared V4 turnover/cost overlay."""
    valued=px.ffill();shares={};cash=1.;points=[];logs=[];w=weights_for(alpha,reverse)
    for date in dates:
        current={t:sh*float(valued.at[date,t]) for t,sh in shares.items()}
        before=cash+sum(current.values())
        if date in targets:
            signal,tickers=targets[date]
            detail=compare_weights({t:v/before for t,v in current.items()},dict(zip(tickers,w)),initial=not logs)
            shares={t:before*weight/float(px.at[date,t]) for t,weight in zip(tickers,w)}
            cash=before-sum(sh*float(px.at[date,t]) for t,sh in shares.items())
            logs.append(dict(signal_date=signal,entry_date=date,tickers=','.join(tickers),**detail))
        points.append(cash+sum(sh*float(valued.at[date,t]) for t,sh in shares.items()))
    gross=pd.Series(points,index=dates);gross=gross/gross.iloc[0]
    net,dd,h,stats=apply_costs(gross,pd.DataFrame(logs),gross_stats(gross),cost)
    return net,h,stats,gross


def rank_summary(frame,keys):
    result=[]
    for key,g in frame.groupby(keys):
        key=key if isinstance(key,tuple) else (key,)
        x=g.holding_return.dropna()
        result.append(dict(zip(keys,key),observations=len(x),excluded=int(g.holding_return.isna().sum()),
            mean=x.mean(),median=x.median(),win_rate=x.gt(0).mean() if len(x) else np.nan,
            std=x.std(ddof=1),q25=x.quantile(.25),q75=x.quantile(.75),sample_status='Small sample (<10)' if len(x)<10 else 'Descriptive'))
    return pd.DataFrame(result)


def study(ranked,history,equity,prices,cost=5):
    h=history.copy();h.entry_date=pd.to_datetime(h.entry_date);h.signal_date=pd.to_datetime(h.signal_date)
    if len(h)<2 or h.entry_date.duplicated().any() or not h.entry_date.is_monotonic_increasing:
        raise ValueError('At least two ordered production executions are required')
    px=ranked.pivot_table(index='trade_date',columns='ts_code',values='adj_close',aggfunc='last').sort_index()
    dates=pd.DatetimeIndex(equity.index);targets={};ranks={}
    for row in h.itertuples():
        selected=select_target_rows(ranked,'Top 10',row.signal_date)
        tickers=selected.ts_code.astype(str).tolist()
        if len(tickers)!=10 or tickers!=str(row.tickers).split(',') or selected.momentum_rank.tolist()!=list(range(1,11)):
            raise ValueError(f'{row.signal_date.date()}: saved selection/rank order is not an exact production Top10')
        if not row.signal_date<row.entry_date or row.entry_date not in dates:
            raise ValueError('Signal/execution alignment mismatch')
        if not (np.isfinite(px.loc[row.entry_date,tickers])&px.loc[row.entry_date,tickers].gt(0)).all():
            raise ValueError('Invalid execution price: weighting comparison withheld')
        targets[row.entry_date]=(row.signal_date,tickers);ranks[row.signal_date]=selected
    original=equity.copy()
    rebuilt,rebuilt_h,_,_=replay(px,dates,targets,0.,cost)
    error=float(np.max(np.abs(rebuilt.to_numpy()-original.to_numpy())))
    if not np.allclose(rebuilt,original,rtol=1e-9,atol=1e-10) or not np.allclose(rebuilt_h.one_way_turnover,h.one_way_turnover,rtol=1e-9,atol=1e-10):
        raise ValueError(f'Equal-weight reconstruction mismatch (maximum NAV error {error:g}); interpretation stopped')
    end=h.entry_date.iloc[-1];evaluation=dates[dates<=end]
    used={k:v for k,v in targets.items() if k<end}
    curves={};summaries=[];annual=[];rebalance=[];definitions=[];period_results=[]
    for name,(alpha,reverse) in METHODS.items():
        net,log,s,gross=replay(px,evaluation,used,alpha,cost,reverse);curves[name]=net
        daily=net.pct_change(fill_method=None);daily.iloc[0]=net.iloc[0]-1
        vol=float(daily.std(ddof=1)*np.sqrt(252))
        summaries.append(dict(method=name,alpha=alpha,reverse=reverse,cumulative_net_return=s['net_return'],cagr=s['net_cagr'],maximum_drawdown=s['net_max_drawdown'],
            volatility=vol,sharpe_rf0=float(s['net_cagr']/vol) if vol else None,
            average_turnover=s['average_turnover'],total_cost_per_initial_nav=s['total_cost_paid'],completed_periods=len(h)-1,recurring_rebalances=s['recurring_rebalances'],
            gross_return=s['gross_return'],cost_drag=s['trading_cost_drag'],hhi=float(sum(weights_for(alpha,reverse)**2)),effective_holdings=float(1/sum(weights_for(alpha,reverse)**2))))
        for year,g in daily.groupby(daily.index.year):
            path=(1+g).cumprod();a=np.r_[1.,path.to_numpy()]
            annual.append(dict(method=name,year=int(year),net_return=float(path.iloc[-1]-1),maximum_drawdown=float((a/np.maximum.accumulate(a)-1).min()),
                partial=bool(year in (evaluation[0].year,evaluation[-1].year))))
        log['method']=name;rebalance.append(log)
        for rank,w in enumerate(weights_for(alpha,reverse),1):definitions.append(dict(method=name,alpha=alpha,reverse=reverse,rank=rank,weight=w))
        for i,row in log.iterrows():
            finish=float(log.iloc[i+1].nav_before) if i+1<len(log) else float(net.iloc[-1])
            period_results.append(dict(method=name,signal_date=row.signal_date,execution_date=row.entry_date,holding_end_date=h.entry_date.iloc[i+1],net_return=finish/row.nav_before-1))
    reference=original.reindex(evaluation).copy();reference.iloc[-1]=float(h.iloc[-1].nav_before)
    if not np.allclose(curves['Equal weight'],reference,rtol=1e-9,atol=1e-10):raise ValueError('Completed-period baseline mismatch; interpretation stopped')
    sessions=open_sessions(prices.attrs.get('exchange_calendar'));observations=[];association=[];attribution=[];retention=[]
    for i in range(len(h)-1):
        row=h.iloc[i];finish=h.entry_date.iloc[i+1];selected=ranks[row.signal_date]
        needed=sessions[(sessions>=row.entry_date)&(sessions<=finish)]
        local=[]
        for r in selected.itertuples():
            window=px.reindex(index=needed,columns=[r.ts_code]).iloc[:,0]
            missing=int((~np.isfinite(window)|window.le(0)).sum())
            valid=len(needed)>0 and row.entry_date in needed and finish in needed and not missing
            ret=float(window.iloc[-1]/window.iloc[0]-1) if valid else np.nan
            record=dict(signal_date=row.signal_date,execution_date=row.entry_date,holding_end_date=finish,ticker=r.ts_code,rank=int(r.momentum_rank),mom10=float(r.momentum_score),holding_return=ret,
                validity='Valid' if valid else f'Excluded: {missing} missing/invalid observations or unavailable exchange calendar',year=row.signal_date.year)
            observations.append(record);local.append(ret)
        values=pd.Series(local)
        corr=pd.Series(np.arange(10,0,-1)).rank().corr(values.rank()) if values.notna().all() else np.nan
        association.append(dict(signal_date=row.signal_date,strength_spearman=corr,complete_top10=bool(values.notna().all())))
        if values.notna().all():
            for name,(alpha,reverse) in METHODS.items():
                attribution.append(dict(signal_date=row.signal_date,method=name,gross_sizing_effect=float(np.dot(weights_for(alpha,reverse),values)-values.mean()),top3_mean=float(values.iloc[:3].mean()),bottom3_mean=float(values.iloc[-3:].mean())))
        if i:
            kept=len(set(str(row.tickers).split(','))&set(str(h.iloc[i-1].tickers).split(',')))
            retention.append(dict(signal_date=row.signal_date,retained=kept,persistence=kept/10,bucket='0' if kept==0 else '1–2' if kept<=2 else '3–4' if kept<=4 else '5–6' if kept<=6 else '7–10',
                holding_return=float(h.iloc[i+1].nav_before/row.nav_before-1) if values.notna().all() else np.nan))
    obs=pd.DataFrame(observations);summary=pd.DataFrame(summaries);base=summary.iloc[0]
    for key in ['cumulative_net_return','cagr','maximum_drawdown','average_turnover','total_cost_per_initial_nav']:
        summary['delta_'+key]=summary[key]-base[key]
    assoc=pd.DataFrame(association);effect=pd.DataFrame(attribution);rankstats=rank_summary(obs,['rank'])
    mean_assoc=float(assoc.strength_spearman.mean());monotonic=bool(rankstats['mean'].notna().all() and rankstats['mean'].is_monotonic_decreasing)
    notes=[f'Full production equal-weight reconstruction verified; maximum NAV discrepancy {error:.3g}.',
        'Signal ranks and selected tickers match production exactly. Existing classification-vintage and historical-membership limitations still apply.',
        'Completed execution-to-execution periods only; final execution is an endpoint, with its new allocation/cost excluded. Original full-run results are untouched.',
        'Uses production adjusted-close execution and gross share valuation; shared securities-only half-L1 turnover and cost overlay, including initial funding. No cash-participation overlay is applied.',
        f'{int(obs.holding_return.isna().sum())} ticker-period returns excluded for missing observations/calendar evidence. Portfolio curves retain the production forward-valuation convention; any such results are provisional.',
        'Rank returns are gross security returns. Portfolio returns include modeled costs. Average turnover excludes initial funding; total costs are per initial NAV.',
        'The displayed rf=0 ratio is net CAGR / annualized volatility, matching the existing Research Lab convention; it is not a regression alpha.',
        'Positive strength-Spearman means stronger MOM ranks tended to have higher returns. Period correlations are averaged, not pooled; descriptive only, no independent-observation significance claim.',
        'Gradient sizing and concentration cannot be causally separated by these portfolios. HHI/effective holdings describe concentration; complete-period gross sizing effects and gross/net cost drags describe contributions, not causal proof.',
        'Annual rank statistics use signal year; annual portfolio returns use valuation year. Boundary years are conservatively marked partial. Small samples (<10) are flagged.',
        'Predefined alpha 0, 0.5 and 1 only, with forward and reverse rank ordering. Reverse weights are proportional to rank^alpha, so rank 10 receives most weight. No tuning, forecasting, confidence claims or production recommendation.']
    conclusion=f'Mean within-period strength/forward-return Spearman: {mean_assoc:.3f}. Rank means '+('are' if monotonic else 'are not')+' monotonically decreasing from rank 1 to 10. This descriptive evidence alone does not establish persistent sizing value; inspect annual stability, concentration and costs.'
    if len(effect):
        top_bottom=float((effect[effect.method=='Equal weight'].top3_mean-effect[effect.method=='Equal weight'].bottom3_mean).mean())
        conclusion+=f' On complete Top10 periods, ranks 1–3 minus ranks 8–10 averaged {top_bottom*100:+.3f} percentage points per period.'
    gains=summary.loc[summary.method!='Equal weight','delta_cumulative_net_return']
    conclusion+=' None of the gradients improved cumulative net return over equal weight.' if not gains.gt(0).any() else ' A higher-ending gradient curve alone cannot establish a reliable rank advantage.'
    tables=dict(rank_observations=obs,rank_summary=rankstats,annual_rank=rank_summary(obs,['year','rank']),weighting_definitions=pd.DataFrame(definitions),rebalance_records=pd.concat(rebalance,ignore_index=True),holding_periods=pd.DataFrame(period_results),annual_comparison=pd.DataFrame(annual),strategy_comparison=summary,rank_association=assoc,sizing_attribution=effect,leader_retention=pd.DataFrame(retention))
    if retention:tables['leader_summary']=rank_summary(pd.DataFrame(retention),['bucket'])
    return dict(status='available',notes=notes,conclusion=conclusion,reconciliation_error=error,start=str(evaluation[0].date()),end=str(end.date()),mean_strength_spearman=mean_assoc),pd.DataFrame(curves),tables


def save_report(ranked,history,equity,prices,cost,out,source):
    import matplotlib.pyplot as plt
    report,curves,tables=study(ranked,history,equity,prices,cost)
    out=Path(out)/'rank_weighting_research';out.mkdir(parents=True,exist_ok=True);files={};charts={}
    tables['equity_curves']=curves
    for name,table in tables.items():
        path=out/(name+'.csv');table.to_csv(path,index=name=='equity_curves',encoding='utf-8-sig');files[name]=str(path)
    for name in ['rank_returns','equity','annual_returns','turnover']:
        fig,ax=plt.subplots(figsize=(11,5))
        try:
            if name=='rank_returns':
                t=tables['rank_summary'];ax.plot(t['rank'],t['mean']*100,'o-',label='Mean');ax.plot(t['rank'],t['median']*100,'o-',label='Median');ax.set_xlabel('Production MOM10 rank (1 strongest)');ax.set_ylabel('Subsequent security return (%) · gross');ax.set_xticks(range(1,11))
            elif name=='equity':
                for col in curves:ax.plot(curves.index,(curves[col]-1)*100,label=col,linestyle='--' if col.startswith('Reverse') else '-')
                ax.set_ylabel('Cumulative net return (%)');ax.set_xlabel('Valuation date · completed periods only')
            elif name=='annual_returns':
                t=tables['annual_comparison'];t.pivot(index='year',columns='method',values='net_return').mul(100).plot.bar(ax=ax);ax.set_ylabel('Calendar-year net return (%)');ax.set_xlabel('Valuation year · first/last years partial')
            else:
                t=tables['strategy_comparison'];ax.bar(t.method,t.average_turnover*100,label='Recurring rebalance average');ax.set_ylabel('Actual weight-based turnover (%)')
            ax.axhline(0,color='grey',linewidth=.6);ax.grid(alpha=.2);ax.legend();ax.set_title('ETF MOM10 / Top10 / 10D · rank-weighting research · retrospective');fig.tight_layout();path=out/(name+'.png');fig.savefig(path,dpi=120);charts[name]=str(path)
        finally:plt.close(fig)
    report.update(charts=charts,files=files,summary=tables['strategy_comparison'].to_dict('records'),rank_summary=tables['rank_summary'].to_dict('records'),annual=tables['annual_comparison'].to_dict('records'),annual_rank=tables['annual_rank'].to_dict('records'),leader_summary=tables.get('leader_summary',pd.DataFrame()).to_dict('records'),source=source)
    files['methodology']=str(out/'methodology.json');(out/'methodology.json').write_text(dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return sanitize_for_json(report)

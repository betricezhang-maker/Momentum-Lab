"""Predefined cash-warning feasibility diagnostics; never a live recommendation."""
from pathlib import Path
import numpy as np
import pandas as pd
from .serialization import dumps,sanitize_for_json

RULES={'Low participation':(0,),'Weak Top10 MOM10':(1,),'Negative median60':(2,),
       'Low participation AND weak MOM10':(0,1),'Low participation AND negative median60':(0,2),
       'Weak MOM10 AND negative median60':(1,2),'All three':(0,1,2)}
COLS=['positive_participation','average_top10_mom10','median_return60']


def warnings_for(d,thresholds,rule):
    columns=[COLS[i] for i in RULES[rule]]
    available=d[columns].notna().all(axis=1)
    tests=[d[COLS[0]].lt(thresholds[0]),d[COLS[1]].lt(thresholds[1]),d[COLS[2]].lt(0)]
    flag=available.copy()
    for i in RULES[rule]:flag &= tests[i]
    return flag,available


def economic_path(frame,flags,nav,history,switch_rate):
    """Saved daily net-return segments; cash=0; conservative additive switch charge.

    Invested segments retain production costs PLUS a charge on cash re-entry.
    This avoids claiming an exact hypothetical order simulation.
    """
    value=base=1.;cash=False;points=[];switches=0
    for i,row in frame.iterrows():
        entry=row.execution_date;end=row.holding_end_date
        segment=nav.loc[entry:end].copy();before=float(history.loc[entry,'nav_before']);finish=float(history.loc[end,'nav_before'])
        if segment.empty or segment.isna().any():raise ValueError('Saved daily NAV does not cover evaluation interval')
        segment.iloc[-1]=finish
        target=bool(flags.loc[i]);changed=target!=cash
        if changed:value*=1-switch_rate;switches+=int(changed)
        for date,x in segment.items():
            points.append(dict(date=date,baseline=base*float(x)/before,cash_warning=value if target else value*float(x)/before))
        base*=finish/before
        if not target:value*=finish/before
        cash=target
    curve=pd.DataFrame(points).drop_duplicates('date',keep='last').set_index('date')
    stats={}
    for name in ('baseline','cash_warning'):
        a=np.r_[1.,curve[name].to_numpy()];stats[name+'_return']=float(a[-1]-1);stats[name+'_drawdown']=float((a/np.maximum.accumulate(a)-1).min())
    stats.update(return_difference=stats['cash_warning_return']-stats['baseline_return'],switches=switches)
    return curve,stats


def study(observations,nav,history,cost_bps=5,min_train=20):
    d=observations.copy(deep=True)
    for c in ('signal_date','execution_date','holding_end_date'):d[c]=pd.to_datetime(d[c],errors='coerce')
    d=d.sort_values('signal_date').reset_index(drop=True)
    for c in COLS+['subsequent_net_return']:d[c]=pd.to_numeric(d[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
    d['valid_outcome']=d.holding_return_status.fillna('').str.startswith('Complete:')&d.subsequent_net_return.notna()
    complete=d.holding_end_date.notna() & (d.signal_date<d.execution_date)&(d.execution_date<d.holding_end_date)
    windows=d[complete].sort_values('execution_date')
    if windows.empty:raise ValueError('No complete holding-period boundaries')
    if (windows.execution_date.iloc[1:].to_numpy()<windows.holding_end_date.cummax().iloc[:-1].to_numpy()).any():raise ValueError('Overlapping holding periods: cash-path comparison withheld')
    if (windows.execution_date.iloc[1:].to_numpy()!=windows.holding_end_date.iloc[:-1].to_numpy()).any():raise ValueError('Noncontiguous holding periods: cannot stitch an economic comparison safely')
    h=history.copy();h['entry_date']=pd.to_datetime(h.entry_date);h=h.set_index('entry_date')
    if h.index.duplicated().any():raise ValueError('Duplicate saved execution records')
    for row in windows.itertuples():
        before=float(h.loc[row.execution_date,'nav_before']);after=float(h.loc[row.holding_end_date,'nav_before'])
        if not np.isfinite([before,after]).all() or min(before,after)<=0:raise ValueError('Invalid saved NAV evidence')
        if row.valid_outcome and not np.isclose(row.subsequent_net_return,after/before-1,rtol=1e-9,atol=1e-10):raise ValueError('Diagnostic holding return disagrees with saved production NAV')
        if 'signal_date' in h and pd.Timestamp(h.loc[row.execution_date,'signal_date'])!=row.signal_date:raise ValueError('Diagnostic signal/execution join disagrees with saved backtest')
    nav=nav.copy();nav.index=pd.to_datetime(nav.index)
    rate=float(cost_bps)/10000*.5
    if not 0<=rate<1:raise ValueError('Invalid modeled cost')
    folds=[];decisions=[];metrics=[];curves={};oos_flags={k:pd.Series(False,index=d.index) for k in RULES};oos_indexes=[]
    for year,test in d[complete].groupby(d.loc[complete,'signal_date'].dt.year):
        cutoff=test.signal_date.min();train=d[complete & d.valid_outcome & (d.holding_end_date<cutoff)]
        counts=train[COLS[:2]].notna().sum()
        if len(train)<min_train or (counts<min_train).any():
            folds.append(dict(year=int(year),status='Insufficient prior training history',train_n=len(train)));continue
        thresholds=[float(train[c].quantile(1/3)) for c in COLS[:2]]
        folds.append(dict(year=int(year),status='Evaluated unseen year',train_n=len(train),test_n=len(test),cutoff=cutoff,participation_threshold=thresholds[0],mom10_threshold=thresholds[1],last_training_end=train.holding_end_date.max()))
        oos_indexes.extend(test.index)
        for rule in RULES:
            flag,available=warnings_for(test,thresholds,rule)
            # Flags are determined only by signal-date information, never outcomes.
            oos_flags[rule].loc[test.index]=flag
            for i,r in test.iterrows():
                outcome='No decision: missing indicator' if not available.loc[i] else 'Not flagged'
                if flag.loc[i]:outcome='Unassessable outcome' if not r.valid_outcome else 'Correct warning (loss)' if r.subsequent_net_return<0 else 'False alarm (non-loss)'
                decisions.append(dict(rule=rule,year=int(year),signal_date=r.signal_date,execution_date=r.execution_date,holding_end_date=r.holding_end_date,flagged=bool(flag.loc[i]),indicators_available=bool(available.loc[i]),outcome=outcome,subsequent_net_return=r.subsequent_net_return,participation=r.positive_participation,top10_mom10=r.average_top10_mom10,median60=r.median_return60,participation_threshold=thresholds[0],mom10_threshold=thresholds[1]))
            training_span=d[complete & (d.holding_end_date<cutoff)]
            for label,frame in [('Training (in-sample)',training_span),('Unseen year (OOS)',test)]:
                f,a=warnings_for(frame,thresholds,rule);valid=frame.valid_outcome;y=frame.subsequent_net_return;assessed=f&valid
                metrics.append(dict(rule=rule,period=str(year),evaluation=label,n=len(frame),available=int(a.sum()),flagged=int(f.sum()),assessed_flagged=int(assessed.sum()),losses=int((valid&y.lt(0)).sum()),losses_identified=int((assessed&y.lt(0)).sum()),loss_recall=float((assessed&y.lt(0)).sum()/(valid&y.lt(0)).sum()) if (valid&y.lt(0)).sum() else None,false_alarm_frequency=float((assessed&y.ge(0)).sum()/assessed.sum()) if assessed.sum() else None,positive_returns_missed=float(y[assessed&y.gt(0)].sum()),negative_returns_avoided=float(-y[assessed&y.lt(0)].sum()),sample_status='Small flagged sample (<10)' if assessed.sum()<10 else 'Descriptive only'))
                _,s=economic_path(frame,f,nav,h,rate);metrics[-1].update(s)
    if not oos_indexes:raise ValueError('No unseen year has sufficient training history')
    evaluation=d.loc[sorted(set(oos_indexes))]
    aggregate=[]
    for rule in RULES:
        curve,s=economic_path(evaluation,oos_flags[rule],nav,h,rate);curves[rule]=curve
        flag=oos_flags[rule].loc[evaluation.index];y=evaluation.subsequent_net_return;assessed=flag&evaluation.valid_outcome
        # Nonoverlapping chronological blocks of per-period cash benefit retain local dependence.
        before=h.loc[evaluation.execution_date,'nav_before'].to_numpy();after=h.loc[evaluation.holding_end_date,'nav_before'].to_numpy();base_returns=after/before-1
        switches=flag.ne(flag.shift(fill_value=False));hyp=np.where(flag,0.,base_returns);hyp=(1+hyp)*(1-rate*switches.to_numpy())-1
        delta=hyp-base_returns;ci=None
        if len(delta)>=30 and evaluation.valid_outcome.all():
            rng=np.random.default_rng(1729);starts=rng.integers(0,len(delta)-4,(500,int(np.ceil(len(delta)/5))));ix=(starts[:,:,None]+np.arange(5)).reshape(500,-1)[:,:len(delta)];ci=np.quantile(delta[ix].mean(axis=1),[.025,.975]).tolist()
        aggregate.append(dict(rule=rule,n=len(evaluation),flagged=int(flag.sum()),assessed_flagged=int(assessed.sum()),unassessable_outcomes=int((~evaluation.valid_outcome).sum()),positive_returns_missed=float(y[assessed&y.gt(0)].sum()),negative_returns_avoided=float(-y[assessed&y.lt(0)].sum()),loss_recall=float((assessed&y.lt(0)).sum()/(evaluation.valid_outcome&y.lt(0)).sum()) if (evaluation.valid_outcome&y.lt(0)).sum() else None,false_alarm_frequency=float((assessed&y.ge(0)).sum()/assessed.sum()) if assessed.sum() else None,mean_benefit_ci95=ci,**s))
    improving=[x['rule'] for x in aggregate if x['return_difference']>0]
    conclusion=('None of the seven predefined warnings improves aggregate out-of-sample cumulative return versus uninterrupted Mom101010 under the documented assumptions. No reliable cash-warning economic benefit is demonstrated.' if not improving else 'Some predefined warnings improve retrospective out-of-sample cumulative return, but this alone does not establish reliable advance warning; annual consistency, sample uncertainty, data limitations and prospective validation remain necessary.')
    result=dict(coverage={'input_signals':len(d),'complete_execution_intervals':int(complete.sum()),'oos_intervals':len(evaluation),'unassessable_oos_outcomes':int((~evaluation.valid_outcome).sum()),'missing_indicator_decisions':int(sum(not x['indicators_available'] for x in decisions))},folds=folds,metrics=metrics,aggregate=aggregate,decisions=decisions,cost_bps=cost_bps,switch_charge=rate,cash_return=0,
        improving_conditions=improving,conclusion=conclusion+' Exploratory only; no live recommendation.',
        limitations=[
            'Seven predefined conditions; lower-third thresholds frozen yearly from completed prior periods. Negative median60 uses zero. Ties at thresholds are not flagged; zero is non-loss.',
            'Training statistics reuse calibration observations and are optimistic. Yearly thresholds are unseen-observation tests, but this retrospective research is not a prospectively registered validation.',
            'Cash earns 0%. Each cash/stock state change costs 0.5 times configured basis-point rate (production one-way turnover convention). Invested intervals retain original production costs PLUS re-entry charge: conservative proxy, not exact alternate orders.',
            'Unassessable held-price periods remain in economic curves using unchanged saved NAV fallback. Flags never depend on outcome availability. These periods invalidate a strong economic inference; mean-benefit CIs are withheld when any are present.',
            'Missing required indicators produce no decision and retain baseline exposure. Final incomplete period excluded. Annual equity comparisons restart invested at 1; aggregate retains state across years. No terminal liquidation charge.',
            'Positive returns missed and losses avoided are arithmetic sums of period returns, not cumulative investment returns. Economic differences use compounded daily curves.',
            'Loss recall = flagged losses / assessable losses; false alarms = flagged non-losses / assessable flagged periods. No exhaustive search, model fitting, or selection based on 2022.',
            'If supported, 95% mean-benefit intervals use five-observation chronological blocks, 500 replicates; no multiple-testing adjustment. Overlapping windows block this study.',
            'Point-in-time eligibility uses production snapshots; historical classification vintages and vendor revisions are not independently reconstructable.'])
    return sanitize_for_json(result),curves


def save_report(observations,nav,history,folder,cost_bps=5,source=None):
    import matplotlib.pyplot as plt
    folder=Path(folder);result,curves=study(observations,nav,history,cost_bps)
    result['source']=source
    for key in ('metrics','aggregate','decisions','folds'):pd.DataFrame(result[key]).to_csv(folder/f'cash_warning_{key}.csv',index=False,encoding='utf-8-sig')
    fig,axes=plt.subplots(4,2,figsize=(13,14));allcurves=[]
    for ax,(rule,curve) in zip(axes.flat,curves.items()):
        ax.plot(curve.index,(curve.baseline-1)*100,label='Uninterrupted production baseline (same OOS range)')
        ax.plot(curve.index,(curve.cash_warning-1)*100,label='Hypothetical warning / cash')
        ax.set_title(rule,fontsize=9);ax.set_ylabel('Cumulative return (%)');ax.grid(alpha=.2);ax.legend(fontsize=6)
        allcurves.append(curve.reset_index().assign(rule=rule))
    axes.flat[-1].axis('off');fig.suptitle('Cash-Warning Research · retrospective walk-forward · conservative cost proxy\nMissing-held-price valuation limitations apply; no live recommendation')
    fig.tight_layout();chart=folder/'cash_warning_equity.png';fig.savefig(chart,dpi=130);plt.close(fig)
    pd.concat(allcurves).to_csv(folder/'cash_warning_curves.csv',index=False,encoding='utf-8-sig')
    result['chart']=str(chart);result['files']={name:str(folder/name) for name in ['cash_warning_'+k+'.csv' for k in ('metrics','aggregate','decisions','folds','curves')]+['cash_warning_report.json']}
    (folder/'cash_warning_report.json').write_text(dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    return result

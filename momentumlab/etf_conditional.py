"""Descriptive diagnostics over saved observations; no trading-engine calls."""
from pathlib import Path
import numpy as np
import pandas as pd
from .serialization import dumps, sanitize_for_json


def analyze(observations, min_prior=20, block=5, reps=500):
    if min_prior<2 or block<2 or reps<100:raise ValueError('Invalid diagnostic sample settings')
    d=observations.copy(deep=True)
    for c in ('signal_date','execution_date','holding_end_date'):d[c]=pd.to_datetime(d[c],errors='coerce')
    d=d.sort_values('signal_date').reset_index(drop=True)
    if d.signal_date.isna().any() or d.signal_date.duplicated().any():raise ValueError('Signal dates must be unique and valid')
    for c in ('positive_participation','average_top10_mom10','median_return60','subsequent_net_return'):
        d[c]=pd.to_numeric(d[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
    past=[];mom_past={};rows=[]
    for r in d.itertuples():
        threshold=float(np.median(past)) if len(past)>=min_prior else np.nan
        reason='';group='Excluded'
        if pd.isna(r.positive_participation) or pd.isna(r.median_return60):reason='Missing indicator'
        elif pd.isna(threshold):reason='Insufficient preceding participation history'
        elif r.median_return60==0:reason='Exactly zero 60-session return (separate neutral category)'
        else:group=('Low' if r.positive_participation<=threshold else 'High')+' / '+('Negative' if r.median_return60<0 else 'Positive')
        if pd.notna(r.positive_participation):past.append(r.positive_participation)
        prior=mom_past.setdefault(group,[]);mom_threshold=float(np.median(prior)) if group!='Excluded' and len(prior)>=10 else np.nan
        mom_bin='Unavailable' if pd.isna(mom_threshold) or pd.isna(r.average_top10_mom10) else 'Low MOM10' if r.average_top10_mom10<=mom_threshold else 'High MOM10'
        if group!='Excluded' and pd.notna(r.average_top10_mom10):prior.append(r.average_top10_mom10)
        complete=str(r.holding_return_status).startswith('Complete:') and pd.notna(r.subsequent_net_return)
        complete=complete and pd.notna(r.execution_date) and pd.notna(r.holding_end_date) and r.signal_date<r.execution_date<r.holding_end_date
        if not complete:reason=(reason+'; ' if reason else '')+'Incomplete/unavailable realized holding period'
        rows.append(dict(participation_threshold=threshold,group=group,mom_threshold=mom_threshold,mom_bin=mom_bin,exclusion_reason=reason,eligible=not reason))
    d=pd.concat([d,pd.DataFrame(rows)],axis=1)
    windows=d[d.eligible].sort_values('execution_date')
    overlap=bool((windows.execution_date.iloc[1:].to_numpy()<windows.holding_end_date.cummax().iloc[:-1].to_numpy()).any()) if len(windows)>1 else False
    eligible=d[d.eligible & d.subsequent_net_return.gt(0)]
    outlier=int(eligible.subsequent_net_return.idxmax()) if len(eligible) else None
    summaries=[]
    groups=[a+' / '+b for a in ('Low','High') for b in ('Negative','Positive')]
    for mode in ('Original','Exclude largest positive'):
        # Keep thresholds and the time grid fixed; only mask the selected outcome.
        use=d.eligible.copy()
        if mode!='Original' and outlier is not None:use.loc[outlier]=False
        periods=[('All',d.index)]+[(str(year),x.index) for year,x in d.groupby(d.signal_date.dt.year)]
        for period,idx in periods:
            frame=d.loc[idx];n=len(frame);rng=np.random.default_rng(1729)
            sampled=None
            if not overlap and n>=max(20,2*block):
                starts=rng.integers(0,n-block+1,(reps,int(np.ceil(n/block))))
                sampled=(starts[:,:,None]+np.arange(block)).reshape(reps,-1)[:,:n]
            for group in groups:
                for mom_bin in ('All','Low MOM10','High MOM10'):
                    mask=use.loc[idx]&frame.group.eq(group)
                    if mom_bin!='All':mask &= frame.mom_bin.eq(mom_bin)
                    values=frame.subsequent_net_return.to_numpy();good=mask.to_numpy();v=values[good];count=len(v)
                    ci=None
                    if sampled is not None and count>=20:
                        means=[values[j][good[j]].mean() for j in sampled if good[j].sum()>=2]
                        if len(means)>=reps*.9:ci=np.quantile(means,[.025,.975]).tolist()
                    q=np.quantile(v,[0,.25,.5,.75,1]).tolist() if count else [None]*5
                    summaries.append(dict(mode=mode,period=period,group=group,mom_bin=mom_bin,n=count,
                        mean=float(v.mean()) if count else None,median=q[2],profitable_frequency=float((v>0).mean()) if count else None,
                        minimum=q[0],q25=q[1],q75=q[3],maximum=q[4],mean_ci95=ci,
                        sample_status='Insufficient observations (<10)' if count<10 else 'Descriptive sample',
                        uncertainty='Overlapping windows: CI withheld' if overlap else 'Moving-block 95% mean CI' if ci else 'CI unavailable: requires >=20 group observations and adequate blocks',
                        mom_unclassified=int((use.loc[idx]&frame.group.eq(group)&frame.mom_bin.eq('Unavailable')).sum())))
    comparison=[]
    for group in groups:
        sub={x['mom_bin']:x for x in summaries if x['mode']=='Original' and x['period']=='All' and x['group']==group}
        lo,hi=sub['Low MOM10'],sub['High MOM10']
        difference=hi['mean']-lo['mean'] if lo['n'] and hi['n'] else None
        comparison.append(f"{group}: low MOM10 n={lo['n']}, high MOM10 n={hi['n']}; "+('mean difference unavailable' if difference is None else f'high-minus-low mean return {difference:.2%}')+'. '+('Insufficient subgroup samples; descriptive only.' if min(lo['n'],hi['n'])<10 else 'Descriptive contrast, not a tested incremental effect.'))
    result=dict(mom_comparison=comparison,settings=dict(min_prior=min_prior,block=block,reps=reps,mom_min_prior_in_group=10),
        rows=len(d),eligible=int(d.eligible.sum()),excluded=d.loc[~d.eligible,'exclusion_reason'].value_counts().to_dict(),
        overlapping_windows=overlap,outlier=None if outlier is None else dict(signal_date=d.loc[outlier,'signal_date'],net_return=d.loc[outlier,'subsequent_net_return']),
        summary=summaries,notes=[
            'Participation split is the expanding median of preceding valid signal observations only; at least 20 required. Ties are Low; zero participation is valid.',
            'Exactly zero median60 is neutral and excluded from the 2x2; missing indicators and incomplete outcomes are explicitly excluded.',
            'Mean Top10 MOM10 comparison splits within each group using its preceding scores only (10 required); unavailable subgroup history is counted. Differences are descriptive, not incremental predictive proof.',
            'Sensitivity removes one globally largest positive eligible holding return; ties remove the earliest signal. Thresholds and all other observations are unchanged. Annual views use this same exclusion.',
            'Calendar years refer to originating signal year. Chronological moving blocks preserve local dependence; block length five holding observations, deterministic seed. Overlaps suppress CIs. No IID inference, fitted model, causal claim or multiple-testing adjustment.',
            'Production eligibility/classification, adjusted-price history and missing-held-price limitations of the source run remain applicable.'])
    return d,sanitize_for_json(result)


def save_report(observations,folder):
    import matplotlib.pyplot as plt
    folder=Path(folder);d,result=analyze(observations)
    d.to_csv(folder/'etf_conditional_observations.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(result['summary']).to_csv(folder/'etf_conditional_summary.csv',index=False,encoding='utf-8-sig')
    charts={}
    full=[x['mean'] for x in result['summary'] if x['period']=='All' and x['mom_bin']=='All' and x['mean'] is not None]
    limit=max([abs(v)*100 for v in full]+[.01])
    for mode in ('Original','Exclude largest positive'):
        rows=[x for x in result['summary'] if x['mode']==mode and x['period']=='All' and x['mom_bin']=='All']
        values=np.array([np.nan if x['mean'] is None else x['mean']*100 for x in rows]).reshape(2,2)
        fig,ax=plt.subplots(figsize=(8,5));im=ax.imshow(values,cmap='RdYlGn',vmin=-limit,vmax=limit)
        ax.set_xticks([0,1],['Negative','Positive']);ax.set_yticks([0,1],['Low','High'])
        ax.set_xlabel('Median eligible ETF 60-session return');ax.set_ylabel('Participation vs preceding-history median')
        for i,r in enumerate(rows):ax.text(i%2,i//2,('Unavailable' if r['mean'] is None else f"Mean {r['mean']:.2%}")+f"\nn = {r['n']}"+('\nSmall sample' if r['n']<10 else ''),ha='center',va='center')
        ax.set_title('ETF MOM10 / Top10 / 10D · '+mode+'\nSubsequent net holding-period return; descriptive only')
        fig.colorbar(im,ax=ax,label='Mean net holding-period return (%)');fig.tight_layout()
        name='etf_conditional_'+('original' if mode=='Original' else 'sensitivity')+'.png';fig.savefig(folder/name,dpi=130);plt.close(fig);charts[mode]=str(folder/name)
    result['charts']=charts;result['files']={name:str(folder/name) for name in ['etf_conditional_observations.csv','etf_conditional_summary.csv','etf_conditional_report.json']}
    (folder/'etf_conditional_report.json').write_text(dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

"""Targeted provider requests with a full local QFQ rebuild and validated publication."""
from collections import defaultdict
import pandas as pd
from .data_integrity import DATA_LOCK, staged_dataset, canonical_upsert, validate_dataset, publish_dataset, audit_event
from .issue_review import repair_eligible


def _blocking_sessions(report):
    """Compare missing sessions, since range-based issue IDs can split after repair."""
    return {(row['ticker'], row['affected_component'], day)
            for row in report['completeness']['findings'] if row.get('blocks_research')
            for day in row.get('session_dates', [])}


def _safe_partial_publication(before, after):
    old, new = _blocking_sessions(before), _blocking_sessions(after)
    other_errors = [check['message'] for check in after['checks']
                    if check['status'] == 'FAIL' and check['name'] != 'completeness']
    return (after['structural_status'] != 'FAIL'
            and not other_errors and bool(old - new) and new <= old)

def repair_issues(paths, universe, selected_ids, validate, provider, rebuild, progress=lambda **kw:None):
    with DATA_LOCK:
        before=validate();rows={r['issue_id']:r for r in before['completeness']['findings']}
        selected=[rows[i] for i in dict.fromkeys(selected_ids) if i in rows]
        results={i:dict(issue_id=i,status='Not eligible for automatic repair') for i in dict.fromkeys(selected_ids)}
        eligible=[r for r in selected if repair_eligible(r)]
        if not eligible:return dict(ok=True,committed=False,results=list(results.values()),integrity=before)
        # Request only missing source keys. Adjusted-only gaps with complete
        # source evidence need just the existing local QFQ rebuild.
        source_keys={}
        for component,key in (('raw','raw_price_csv'),('factor','adj_factor_csv')):
            frame=pd.read_csv(paths[key],usecols=['ts_code','trade_date'],dtype=str)
            source_keys[component]=set(zip(frame.ts_code.astype(str).str.strip().str.upper(),
                                           frame.trade_date.astype(str).str.replace('-','',regex=False)))
        requests=defaultdict(set)
        for row in eligible:
            for day in row['session_dates']:
                for component in ('raw','factor'):
                    if (row['ticker'],day) not in source_keys[component]:
                        requests[(component,day)].add(row['ticker'])
        tasks=[]
        for (component,day),tickers in sorted(requests.items()):
            if universe=='ETF_250M':tasks.append((component,day,tickers))
            else:tasks.extend((component,day,{ticker}) for ticker in sorted(tickers))
        errors={};fetched=0;committed=False;after=before;backup=None;failure=None
        progress(stage='Planning targeted repair',current=0,total=len(tasks),message=f'{len(eligible)} unresolved findings; {len(tasks)} missing-source requests; full local QFQ rebuild after fetch.')
        try:
            with staged_dataset(paths) as work:
                parts=defaultdict(list)
                for index,(component,day,tickers) in enumerate(tasks,1):
                    api=('fund_daily' if component=='raw' else 'fund_adj') if universe=='ETF_250M' else ('daily' if component=='raw' else 'adj_factor')
                    params={'trade_date':day} if universe=='ETF_250M' else {'ts_code':next(iter(tickers)),'start_date':day,'end_date':day}
                    fields='ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount' if component=='raw' else 'ts_code,trade_date,adj_factor'
                    progress(stage='Fetching missing observations',current=index-1,total=len(tasks),message=f'{api} {day}: {len(tickers)} selected tickers')
                    try:
                        frame=provider(api,params,fields)
                        if not frame.empty:
                            from .data_integrity import normalize_keys
                            frame=normalize_keys(frame,['ts_code','trade_date'])
                            frame=frame[frame.ts_code.isin(tickers)&frame.trade_date.eq(day)].copy()
                            if not frame.empty:parts[component].append(frame);fetched+=len(frame)
                    except Exception as exc:
                        for ticker in tickers:errors[(ticker,day,component)]=str(exc)
                    progress(current=index,total=len(tasks),rows_fetched=fetched)
                derived_only=any(r['affected_component']=='adjusted_price' and not r.get('raw_missing_dates') for r in eligible)
                if not any(parts.values()) and not derived_only:
                    for row in eligible:
                        reasons=[msg for (t,d,c),msg in errors.items() if t==row['ticker'] and d in row['session_dates']]
                        results[row['issue_id']]=dict(issue_id=row['issue_id'],status='Provider error' if reasons else 'Still missing',detail='; '.join(reasons) or 'Successful responses contained no relevant rows.')
                    return dict(ok=True,committed=False,results=list(results.values()),integrity=before,rows_fetched=0)
                for component,key in [('raw','raw_price_csv'),('factor','adj_factor_csv')]:
                    if parts[component]:
                        old=pd.read_csv(work[key],dtype={'ts_code':str,'trade_date':str})
                        canonical_upsert(old,pd.concat(parts[component],ignore_index=True),['ts_code','trade_date']).to_csv(work[key],index=False,encoding='utf-8-sig')
                progress(stage='Rebuilding adjusted prices',message='Rebuilding locally with the unchanged latest-factor QFQ method.')
                rebuild(work['raw_price_csv'],work['adj_factor_csv'],work['adjusted_price_csv'])
                progress(stage='Validating staged dataset',message='Production remains unchanged until validation passes.')
                after=validate_dataset(work,universe_id=universe,mode='full')
                if after['ok'] or _safe_partial_publication(before,after):
                    backup=publish_dataset(work,paths,after['manifest']);committed=True
                else:failure='Staged validation found no safe completeness improvement: '+'; '.join(after['errors'])
        except Exception as exc:failure=str(exc)
        final=validate() # Report production state, never confuse staged rows with repaired data.
        remaining={(r['ticker'],r['affected_component'],d) for r in final['completeness']['findings'] for d in r.get('session_dates',[])}
        for row in eligible:
            unresolved=any((row['ticker'],row['affected_component'],d) in remaining for d in row['session_dates'])
            reasons=[msg for (t,d,c),msg in errors.items() if t==row['ticker'] and d in row['session_dates']]
            status='Repaired and revalidated' if committed and not unresolved else 'Provider error' if reasons else 'Still missing'
            results[row['issue_id']]=dict(issue_id=row['issue_id'],status=status,detail='; '.join(reasons) or failure or ('No relevant source observation returned.' if unresolved else ''))
        old_ids=set(rows);new=[r for r in final['completeness']['findings'] if r['issue_id'] not in old_ids]
        audit_event(paths['root'],'BULK_REPAIR_RESULT',universe,committed=committed,results=list(results.values()))
        return dict(ok=True,committed=committed,backup=backup,results=list(results.values()),newly_discovered=new,integrity=final,rows_fetched=fetched,error=failure)

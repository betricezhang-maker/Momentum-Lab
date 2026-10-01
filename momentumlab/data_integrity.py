"""Daily-key diagnostics and validation shared by incremental data writes."""
import pandas as pd
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import uuid
import json
import hashlib
import copy
import threading
from functools import wraps
import numpy as np
from datetime import datetime,timezone
from .dates import normalize_dates
from .universe_validation import equity_classification
from .serialization import dumps
from .completeness import assess_completeness, evidence_paths, apply_issue_resolutions, finding_signature
from .integrity_notices import freshness_details, SHANGHAI, PUBLICATION_HOUR

DATA_LOCK=threading.RLock()
_CACHE={}
SCHEMA_VERSION='2'

def clear_integrity_cache():
    with DATA_LOCK:_CACHE.clear()

def consistent_read(function):
    @wraps(function)
    def run(*args,**kwargs):
        with DATA_LOCK: return function(*args,**kwargs)
    return run

def canonical_upsert(old,new,keys,replace_snapshots=False):
    old=normalize_keys(old,keys) if old is not None and not old.empty else None
    new=normalize_keys(new,keys) if new is not None and not new.empty else None
    if new is not None:
        unique=new.drop_duplicates()
        if unique.duplicated(keys).any(): raise ValueError('Conflicting duplicate keys within refreshed source batch.')
    if old is not None and new is not None:
        if replace_snapshots: old=old[~old.trade_date.isin(new.trade_date)]
        else:
            old=old[~pd.MultiIndex.from_frame(old[keys]).isin(pd.MultiIndex.from_frame(new[keys]))]
    if old is not None and old.drop_duplicates().duplicated(keys).any():
        raise ValueError('Conflicting historical duplicate keys are not covered by refreshed source rows.')
    frames=[x for x in [old,new] if x is not None]
    return pd.concat(frames,ignore_index=True).drop_duplicates(keys,keep='last').sort_values(keys).reset_index(drop=True) if frames else pd.DataFrame()

def audit_event(root,event,universe,**details):
    path=Path(root)/'integrity_audit.jsonl'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as stream:
        stream.write(dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),event=event,universe=universe,**details))+'\n')

def require_valid(result):
    if result['status']=='FAIL':
        raise ValueError('DATASET INTEGRITY FAIL:\n- '+'\n- '.join(result['errors']))
    return result

def _ack_path(paths): return Path(paths['root']) / 'completeness_acknowledgments.json'

def load_acknowledgements(paths):
    try: return json.loads(_ack_path(paths).read_text(encoding='utf-8'))
    except (OSError, ValueError): return []

def _write_acknowledgements(paths, items):
    target=_ack_path(paths);temp=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(dumps(items,indent=2),encoding='utf-8');temp.replace(target)
    finally: temp.unlink(missing_ok=True)

def save_acknowledgement(paths, issue, category, explanation, reference='', operator='unknown', findings=None, batch_id=None, decision_id=None, source_file=None):
    with DATA_LOCK:
        if issue.get('ticker') == '*' or not issue.get('overridable',False): raise ValueError('This finding is not overridable. Dataset-wide notices cannot confirm ticker-level gaps; structural errors require correction.')
        if not str(explanation).strip(): raise ValueError('A non-empty written explanation is required.')
        items=load_acknowledgements(paths);stamp=datetime.now(timezone.utc).isoformat()
        if decision_id:
            prior=next((x for x in items if x.get('decision_id')==decision_id and not x.get('inherited_from')),None)
            if prior: return prior
        def record(finding,dates,parent=None):
            original=copy.deepcopy(finding)
            for key in ('confirmation','acknowledgement'): original.pop(key,None)
            item=dict(confirmation_id=uuid.uuid4().hex,issue_id=finding['issue_id'],ticker=finding['ticker'],date=min(dates),end_date=max(dates),
                      affected_component=finding['affected_component'],evidence_signature=finding_signature(finding),session_dates=dates,
                      session_signatures={d:finding['session_signatures'][d] for d in dates},
                      category=category or 'MANUALLY_ACCEPTED_GAP',explanation=explanation.strip(),reference=reference.strip(),operator=operator,
                      timestamp=stamp,confirmed_at=stamp,active=True,original_finding=original,import_batch_id=batch_id,decision_id=decision_id,source_file=source_file)
            if parent: item.update(inherited_from=parent['confirmation_id'],parent_issue_id=parent['issue_id'],dependency='Adjusted price cannot be produced without the matching raw price.')
            return item
        item=record(issue,issue.get('session_dates',[]))
        for old in items:
            if old.get('active',True) and (old.get('issue_id')==issue['issue_id'] or old.get('parent_issue_id')==issue['issue_id']):
                old.update(active=False,superseded_at=stamp)
        additions=[item]
        if issue['affected_component']=='raw_price':
            for child in findings or []:
                if child.get('ticker')!=issue['ticker'] or child.get('affected_component')!='adjusted_price' or not child.get('overridable'): continue
                dates=sorted(set(item['session_dates']) & set(child.get('session_dates',[])) & set(child.get('raw_missing_dates',[])))
                # A raw review must never replace an independently recorded adjusted review.
                independent_dates=set()
                for old in items:
                    if old.get('active',True) and not old.get('inherited_from') and old.get('ticker')==child['ticker'] and old.get('affected_component')=='adjusted_price':
                        independent_dates.update(old.get('session_dates') or old.get('original_finding',{}).get('session_dates') or
                                                 [d for d in dates if old.get('date','')<=d<=old.get('end_date','')])
                dates=sorted(set(dates)-independent_dates)
                if dates: additions.append(record(child,dates,item))
        items.extend(additions);_write_acknowledgements(paths,items)
        audit_event(paths['root'],'COMPLETENESS_CONFIRMED',Path(paths['root']).name,confirmation_ids=[x['confirmation_id'] for x in additions],issue_id=item['issue_id'],import_batch_id=batch_id,decision_id=decision_id)
        clear_integrity_cache();return item

def revoke_acknowledgement(paths, issue_id, batch_id=None, decision_id=None):
    with DATA_LOCK:
        items=load_acknowledgements(paths); found=False
        parents={x.get('parent_issue_id',x.get('issue_id')) for x in items if x.get('issue_id')==issue_id}
        parents.add(issue_id)
        for item in items:
            if (item.get('issue_id') in parents or item.get('parent_issue_id') in parents) and item.get('active',True):
                item['active']=False;item['revoked_at']=datetime.now(timezone.utc).isoformat();found=True
        if found:
            _write_acknowledgements(paths,items);audit_event(paths['root'],'COMPLETENESS_REOPENED',Path(paths['root']).name,issue_id=issue_id,import_batch_id=batch_id,decision_id=decision_id);clear_integrity_cache()
        return found

def validate_signal_context(calendar, weights, signal_date, execution_date=None, tickers=None):
    signal=pd.Timestamp(signal_date).strftime('%Y-%m-%d')
    sessions=sorted(pd.Timestamp(d).strftime('%Y-%m-%d') for d in calendar)
    if signal not in sessions: raise ValueError('Integrity FAIL: signal is not an open calendar session.')
    next_day=next((d for d in sessions if d>signal),None)
    if execution_date is not None and pd.Timestamp(execution_date).strftime('%Y-%m-%d')!=next_day:
        raise ValueError('Integrity FAIL: execution is not the next open session.')
    historical=weights[normalize_dates(weights.trade_date)<=pd.Timestamp(signal)]
    if historical.empty: raise ValueError('Integrity FAIL: no point-in-time eligibility snapshot on/before signal; future snapshot prohibited.')
    latest=historical[normalize_dates(historical.trade_date)==normalize_dates(historical.trade_date).max()]
    if tickers and not set(tickers).issubset(set(latest.con_code)):
        raise ValueError('Integrity FAIL: selected securities are absent from point-in-time eligibility.')

def provenance(result,parameters,run_id=None,signal_date=None,execution_date=None):
    manifest=result.get('manifest',{})
    return dict(dataset_id=manifest.get('dataset_id'),dataset_fingerprint=manifest.get('dataset_fingerprint'),
                manifest_version=manifest.get('schema_version'),universe=result.get('universe'),
                strategy_parameters=copy.deepcopy(parameters),strategy_run_id=run_id,
                signal_date=signal_date,execution_date=execution_date,
                run_timestamp=datetime.now(timezone.utc).isoformat(),integrity_status=result.get('status'))

def history_provenance(history,result,parameters,run_id):
    records=history.to_dict('records')
    for record in records:
        record['provenance']=provenance(result,parameters,run_id,record.get('signal_date'),record.get('entry_date',record.get('execution_date')))
    return records


def normalize_keys(frame, keys):
    frame=frame.copy()
    for key in keys:
        if key=='trade_date':
            frame[key]=normalize_dates(frame[key]).dt.strftime('%Y%m%d')
        elif key in {'ts_code','con_code'}:
            frame[key]=frame[key].astype('string').str.strip().str.upper()
    if frame[list(keys)].isna().any().any() or frame[list(keys)].eq('').any().any(): raise ValueError('Invalid or missing daily/security key.')
    return frame


def duplicate_diagnostics(frame, keys=('ts_code','trade_date'), *, normalized=False):
    if not normalized:frame=normalize_keys(frame,keys)
    dup=frame[frame.duplicated(list(keys),keep=False)]
    conflicts=[]
    distinct=dup.drop_duplicates()
    conflicting=distinct[distinct.duplicated(list(keys),keep=False)]
    conflict_count=len(conflicting.drop_duplicates(list(keys)))
    for key,rows in list(conflicting.groupby(list(keys),sort=True))[:3] if conflict_count<100 else []:
        varying=[c for c in rows if rows[c].nunique(dropna=False)>1]
        conflicts.append({'key':list(key),'differences':rows[list(keys)+varying].head(4).to_dict('records')})
    if conflict_count>=100:
        for row in conflicting.drop_duplicates(list(keys)).head(3).to_dict('records'):
            sample=conflicting
            for k in keys: sample=sample[sample[k]==row[k]]
            varying=[c for c in sample if sample[c].nunique(dropna=False)>1]
            conflicts.append({'key':[row[k] for k in keys],'differences':sample[list(keys)+varying].head(4).to_dict('records')})
    return dict(rows=len(frame),first_date=frame.trade_date.min(),last_date=frame.trade_date.max(),
                duplicate_keys=len(dup.drop_duplicates(list(keys))),duplicate_rows=len(dup),
                excess_rows=int(frame.duplicated(list(keys)).sum()),earliest_duplicate_date=dup.trade_date.min() if len(dup) else None,
                latest_duplicate_date=dup.trade_date.max() if len(dup) else None,
                affected_tickers=int(dup[next((k for k in keys if k!='trade_date'),keys[0])].nunique()) if len(dup) and len(keys)>1 else 0,
                conflicting_keys=conflict_count,identical_keys=len(dup.drop_duplicates(list(keys)))-conflict_count,samples=conflicts)


def validate_calendar(frame, through):
    frame=normalize_keys(frame,['trade_date'])
    if frame.empty or 'is_open' not in frame: raise ValueError('Trading calendar is empty or lacks is_open.')
    if not pd.to_numeric(frame.is_open,errors='coerce').isin([0,1]).all(): raise ValueError('Calendar has invalid open/closed flags.')
    if frame.trade_date.max()<pd.Timestamp(through).strftime('%Y%m%d'):
        raise ValueError(f'Trading calendar is stale: covers {frame.trade_date.max()}, requested {through}.')
    if frame.duplicated(['trade_date']).any(): raise ValueError('Trading calendar has duplicate daily keys.')
    return frame.trade_date.max()



def validate_dataset(paths, through=None, universe_id=None, mode='cached', signal_date=None, execution_date=None, lookback=None):
    """One read-only financial-data validator; paths come from the portable resolver.

    Only audit records are written. Cache keys include every input's file identity.
    Optional weight/return fields may be missing; OHLC, factors and keys may not.
    """
    universe=universe_id or Path(paths['root']).name
    specs={
        'trade_calendar_csv':('calendar',['trade_date'],['is_open']),
        'raw_price_csv':('raw_price',['ts_code','trade_date'],['open','high','low','close']),
        'adjusted_price_csv':('adjusted_price',['ts_code','trade_date'],['adj_open','adj_high','adj_low','adj_close']),
        'weights_csv':('eligibility',['con_code','trade_date'],['market_cap_rmb'] if universe=='ETF_250M' else []),
        'adj_factor_csv':('adjustment_factor',['ts_code','trade_date'],['adj_factor'])}
    files={k:Path(paths.get(k,Path(paths['root'])/(k+'.csv'))) for k in specs}
    if universe=='ETF_250M': files['classification_csv']=Path(paths.get('classification_csv',Path(paths['root'])/'fund_classification.csv'))
    files.update(evidence_paths(paths))
    def identity():
        result=[]
        for k,p in files.items():
            try:
                stat=p.stat();result.append((k,str(p.resolve()),stat.st_mtime_ns,stat.st_size,stat.st_ctime_ns))
            except OSError: result.append((k,str(p),None))
        result.append(('publication_pending',(Path(paths['root'])/'.publish_pending.json').exists()))
        return tuple(result)
    with DATA_LOCK:
        signature=identity()
        ack_signature=hashlib.sha256(_ack_path(paths).read_bytes()).hexdigest() if _ack_path(paths).exists() else None
        local_now=datetime.now(SHANGHAI)
        cache_key=(universe,signature,ack_signature,str(through),str(signal_date),str(execution_date),lookback,str(local_now.date()),local_now.hour>=PUBLICATION_HOUR)
        if mode!='full' and cache_key in _CACHE:
            cached=copy.deepcopy(_CACHE[cache_key]);cached['cached']=True;return cached
        if mode=='snapshot':
            raise ValueError('No current validation is cached for this dataset. Click Run integrity check to refresh findings. Browsing does not start a check; research validates the current dataset when requested.')
        root=Path(paths['root']);audit_event(root,'DATA_VALIDATION_STARTED',universe)
        checks=[];frames={};components={};hashes={}
        def check(name,ok,message,details=None,warning=False,info=False):
            checks.append(dict(name=name,status='PASS' if ok else ('INFO' if info else 'WARNING' if warning else 'FAIL'),message=message,details=details or {}))
        check('publication_state',not (root/'.publish_pending.json').exists(),
              'Publication must be complete; an interrupted publication requires backup recovery.')
        for key,(label,keys,numeric) in specs.items():
            path=files[key]
            try:
                frame=pd.read_csv(path,low_memory=False,dtype={k:'string' for k in keys})
                missing=sorted(set(keys+numeric+(['weight'] if label=='eligibility' else []))-set(frame))
                if missing: raise ValueError(f'missing columns: {missing}')
                if frame.empty: raise ValueError('file is empty')
                frame=normalize_keys(frame,keys)
                diagnostics=duplicate_diagnostics(frame,keys,normalized=True)
                components[label]=dict(path=str(path.resolve()),schema=list(frame),
                                       unique_tickers=int(frame[keys[0]].nunique()) if len(keys)>1 else 0,**diagnostics)
                check(label+'_duplicate_keys',diagnostics['duplicate_keys']==0,
                      f"{label}: {diagnostics['duplicate_keys']} duplicate keys ({diagnostics['conflicting_keys']} conflicting)",diagnostics)
                for column in numeric:
                    values=pd.to_numeric(frame[column],errors='coerce')
                    valid=np.isfinite(values).all()
                    if column=='is_open': valid=valid and values.isin([0,1]).all()
                    else: valid=valid and values.gt(0).all()
                    check(label+'_'+column,bool(valid),f'{label}.{column}: must be finite and '+('0/1' if column=='is_open' else 'positive'))
                    frame[column]=values
                prefix='adj_' if label=='adjusted_price' else ''
                if prefix+'high' in frame:
                    check(label+'_ohlc',bool(frame[prefix+'high'].ge(frame[prefix+'low']).all()),f'{label}: high must be >= low')
                for optional in ['adj_daily_return','pre_close','adj_pre_close','weight']:
                    if optional in frame:
                        v=pd.to_numeric(frame[optional],errors='coerce')
                        check(label+'_'+optional,bool((frame[optional].isna()|np.isfinite(v)).all()),f'{label}.{optional}: missing allowed; nonnumeric/infinite values prohibited')
                check(label+'_file',True,f'{label}: readable; schema and daily keys valid')
                frames[label]=frame
                with path.open('rb') as stream:hashes[key]=hashlib.file_digest(stream,'sha256').hexdigest()
            except Exception as exc:
                check(label+'_file',False,f'{label}: {exc}',{'path':str(path.resolve())})
        cal=frames.get('calendar');raw=frames.get('raw_price');adj=frames.get('adjusted_price');fac=frames.get('adjustment_factor');weights=frames.get('eligibility')
        if cal is not None:
            check('calendar_sorted',cal.trade_date.is_monotonic_increasing,'Calendar must be sorted by date')
            if through is not None:
                check('calendar_required_coverage',cal.trade_date.max()>=pd.Timestamp(through).strftime('%Y%m%d'),'Calendar must cover requested-through date')
            open_days=set(cal.loc[cal.is_open.eq(1),'trade_date'])
            if raw is not None:
                absent=sorted(set(raw.trade_date)-open_days)
                check('price_calendar_sessions',not absent,'Raw price dates must be open calendar sessions',{'dates':absent[:20]})
                freshness=freshness_details(cal,raw,adj,through,local_now)
                check('latest_prices',not freshness['raw_session_lag'] and not freshness['adjusted_session_lag'],
                      'Prices may lag calendar: latest valuation could be stale',freshness,warning=True)
            if signal_date is not None:
                signal=pd.Timestamp(signal_date).strftime('%Y%m%d')
                check('signal_session',signal in open_days,'Signal date must be an open session')
                expected=next((d for d in sorted(open_days) if d>signal),None)
                if execution_date is not None:
                    check('next_session_execution',pd.Timestamp(execution_date).strftime('%Y%m%d')==expected,'Execution must be the next open session')
                else: check('execution_calendar_available',expected is not None,'Next execution session is outside calendar coverage',warning=True)
        def keyset(frame): return pd.MultiIndex.from_frame(frame[['ts_code','trade_date']])
        if raw is not None and adj is not None:
            check('raw_adjusted_keys',keyset(raw).equals(keyset(adj)) or (set(keyset(raw))==set(keyset(adj))),
                  'Raw and adjusted security/date keys must match')
        if raw is not None and fac is not None:
            check('factor_coverage',bool(keyset(raw).isin(keyset(fac)).all()),'Adjustment factors must cover every raw price row')
        if all(x is not None for x in (raw,adj,fac)) and not any(x.duplicated(['ts_code','trade_date']).any() for x in (raw,adj,fac)):
            merged=raw[['ts_code','trade_date','open','high','low','close']].merge(fac[['ts_code','trade_date','adj_factor']],on=['ts_code','trade_date'],how='left')
            merged=merged.sort_values(['ts_code','trade_date'])
            ratio=merged['adj_factor']/merged.groupby('ts_code')['adj_factor'].transform('last')
            for column in ['open','high','low','close']:merged['expected_'+column]=merged[column]*ratio
            merged=merged.merge(adj[['ts_code','trade_date','adj_open','adj_high','adj_low','adj_close']],on=['ts_code','trade_date'],how='left')
            matches=all(np.isclose(merged['expected_'+c],merged['adj_'+c],rtol=1e-8,atol=1e-10).all() for c in ['open','high','low','close'])
            check('adjustment_application',bool(matches),'Adjusted OHLC must match the existing factor-ratio formula')
        if raw is not None and weights is not None:
            unknown=sorted(set(raw.ts_code)-set(weights.con_code))
            check('price_universe_membership',not unknown,'Price tickers without any eligibility snapshot may be inactive history',{'tickers':unknown[:20]},warning=True)
            if signal_date is not None:
                eligible=weights[weights.trade_date<=pd.Timestamp(signal_date).strftime('%Y%m%d')]
                check('point_in_time_eligibility',not eligible.empty,'Historical signal requires a snapshot on or before its date')
                if not eligible.empty:
                    latest=eligible[eligible.trade_date==eligible.trade_date.max()]
                    history=raw[raw.trade_date<=pd.Timestamp(signal_date).strftime('%Y%m%d')].groupby('ts_code').size()
                    short=[c for c in latest.con_code if history.get(c,0)<(lookback or 1)]
                    check('signal_warmup',not short,'Some eligible securities lack requested warm-up history',{'tickers':short[:20]},warning=True)
            check('partial_years',raw.trade_date.min()[4:6]=='01' and raw.trade_date.max()[4:6]=='12',
                  'First and last research years may be partial; consult actual run coverage.',
                  dict(first_stored=raw.trade_date.min(),last_stored=raw.trade_date.max(),
                       explanation='Coverage notice: endpoint years may contain fewer than a full year of observations. This does not block research.',
                       action='Interpret endpoint-year results using the displayed dates. No data repair required.'),info=True)
        if universe=='ETF_250M' and weights is not None:
            try:
                metadata=pd.read_csv(files['classification_csv'],dtype={'ts_code':'string'})
                metadata=normalize_keys(metadata,['ts_code'])
                if not {'name','fund_type'}.issubset(metadata): raise ValueError('Classification requires ts_code, name and fund_type')
                allowed=set(equity_classification(metadata).ts_code)
                offenders=weights[~weights.con_code.isin(allowed)].con_code.unique()
                details=metadata[metadata.ts_code.isin(offenders)].to_dict('records')
                details += [dict(ts_code=c,name=None,fund_type='UNKNOWN') for c in offenders if c not in set(metadata.ts_code)]
                for item in details: item['reason_excluded']='Not verified equity/stock classification'
                check('etf_asset_filter',len(offenders)==0,'Equity ETF universe excludes bond, cash, money-market, commodity and gold funds',{'offenders':details})
                check('etf_size',bool(weights.market_cap_rmb.ge(250_000_000).all()),'Historical ETF size must be >= RMB250M')
                with files['classification_csv'].open('rb') as stream:hashes['classification_csv']=hashlib.file_digest(stream,'sha256').hexdigest()
            except Exception as exc: check('etf_asset_filter',False,f'ETF classification evidence unavailable: {exc}')
        structural_status='FAIL' if any(c['status']=='FAIL' for c in checks) else 'WARNING' if any(c['status']=='WARNING' for c in checks) else 'PASS'
        completeness=assess_completeness(frames,paths,through,lookback)
        completeness=apply_issue_resolutions(completeness,load_acknowledgements(paths),frames)
        check('completeness',completeness['status']=='PASS' and not completeness.get('confirmed_count'),
              'Calendar-based completeness: '+completeness['status'],
              {k:v for k,v in completeness.items() if k not in ('findings','confirmation_history')},warning=completeness.get('research_allowed_by_completeness',False))
        for key,path in evidence_paths(paths).items():
            if path.is_file():
                with path.open('rb') as stream: hashes[key]=hashlib.file_digest(stream,'sha256').hexdigest()
        check('files_unchanged',identity()==signature,'Dataset files must not change during validation')
        if checks[-1]['status']=='FAIL': structural_status='FAIL'
        errors=[x['message'] for x in checks if x['status']=='FAIL'];warnings=[x['message'] for x in checks if x['status']=='WARNING']
        status='FAIL' if errors else ('WARNING' if warnings else 'PASS')
        fingerprint=hashlib.sha256(dumps(dict(schema_version=SCHEMA_VERSION,universe=universe,files=hashes),sort_keys=True).encode()).hexdigest()
        stamp=datetime.now(timezone.utc).isoformat()
        manifest=dict(schema_version=SCHEMA_VERSION,universe=universe,created_at=stamp,updated_at=stamp,
                      dataset_fingerprint=fingerprint,dataset_id=f'{universe}_{fingerprint[:16]}',file_hashes=hashes,validation_status=status)
        for label,info in components.items():
            for field in ['first_date','last_date','rows']: manifest[label+'_'+field]=info[field]
        manifest['eligible_tickers']=int(weights.con_code.nunique()) if weights is not None else 0
        manifest['duplicate_price_keys']=sum(components.get(k,{}).get('duplicate_keys',0) for k in ['raw_price','adjusted_price'])
        manifest.update(structural_status=structural_status,completeness_status=completeness['status'],completeness_scope=completeness['scope'])
        result=dict(status=status,structural_status=structural_status,completeness=completeness,universe=universe,timestamp=stamp,checks=checks,manifest=manifest,errors=errors,warnings=warnings,components=components,ok=not errors,
                    research_allowed=not errors,acknowledgements=load_acknowledgements(paths))
        audit_event(root,'DATA_VALIDATION_'+status,universe,dataset_id=manifest['dataset_id'],errors=errors,warnings=warnings)
        if len(_CACHE)>16: _CACHE.clear()
        _CACHE[cache_key]=copy.deepcopy(result)
        return result

def repair_duplicate_keys(paths,universe_id):
    """Explicit repair: reject conflicting values, back up and log every removed key."""
    report=validate_dataset(paths,universe_id=universe_id,mode='full')
    for info in report['components'].values():
        if info['conflicting_keys']: raise ValueError('Conflicting duplicate values require canonical source evidence; no repair performed.')
    audit_event(paths['root'],'DUPLICATE_REPAIR_STARTED',universe_id)
    with staged_dataset(paths) as work:
        for key,security in [('trade_calendar_csv',None),('raw_price_csv','ts_code'),('adjusted_price_csv','ts_code'),('adj_factor_csv','ts_code'),('weights_csv','con_code')]:
            keys=([security] if security else [])+['trade_date']
            frame=normalize_keys(pd.read_csv(work[key]),keys)
            removed=frame[frame.duplicated(keys,keep='first')][keys].to_dict('records')
            audit_event(paths['root'],'DUPLICATE_REPAIR_KEYS',universe_id,component=key,affected_keys=removed)
            frame.drop_duplicates(keys).sort_values(keys).to_csv(work[key],index=False,encoding='utf-8-sig')
        after=require_valid(validate_dataset(work,universe_id=universe_id,mode='full'))
        backup=publish_dataset(work,paths,after['manifest'])
    audit_event(paths['root'],'DUPLICATE_REPAIR_COMPLETED',universe_id,backup=backup,dataset_id=after['manifest']['dataset_id'])
    return dict(report=after,backup=backup)

def diagnose_dataset(paths):
    """Read-only inventory used before any repair or incremental publish."""
    specs = {
        'calendar_csv': ('trade_date',),
        'raw_price_csv': ('ts_code','trade_date'),
        'adj_factor_csv': ('ts_code','trade_date'),
        'weights_csv': ('con_code','trade_date'),
        'adjusted_price_csv': ('ts_code','trade_date'),
    }
    report={}
    for name,keys in specs.items():
        path=paths.get(name) or paths.get({'calendar_csv':'trade_calendar_csv'}.get(name,name))
        if not path or not Path(path).exists():
            report[name]={'exists':False}
            continue
        frame=pd.read_csv(path,low_memory=False)
        report[name]={'exists':True,'path':str(path),**duplicate_diagnostics(frame,keys)}
    return report


@contextmanager
def staged_dataset(paths, resume_id=None):
    """Keep live files untouched until validation; restore backups on publish errors."""
    paths=dict(paths)
    # Local evidence must accompany staged data as well as direct checks.
    for key,path in evidence_paths(paths).items():
        if path.is_file(): paths.setdefault(key,str(path))
    root=Path(paths['root'])
    if resume_id is not None:
        if not resume_id or any(c not in '0123456789abcdef' for c in resume_id):
            raise ValueError('Invalid staged update identity.')
        stage=root/('.incremental-'+resume_id)
        stage.mkdir(parents=True,exist_ok=True)
        working={'root':str(stage)}
        for key,value in paths.items():
            if key=='root': continue
            target=stage/(key+'_'+Path(value).name)
            working[key]=str(target)
            if not target.exists() and Path(value).is_file(): shutil.copy2(value,target)
        if _ack_path(paths).is_file(): shutil.copy2(_ack_path(paths),_ack_path(working))
        try:
            yield working
        except BaseException as exc:
            (stage/'stage_failure.json').write_text(dumps(dict(status='FAIL',message=str(exc),timestamp=datetime.now(timezone.utc).isoformat())),encoding='utf-8')
            audit_event(root,'INCREMENTAL_UPDATE_ROLLED_BACK',root.name,message=str(exc),production_unchanged=True)
            raise
        shutil.rmtree(stage)
        return
    with TemporaryDirectory(prefix='.incremental-',dir=root) as temp:
        stage=Path(temp);working={'root':str(stage)}
        for key,value in paths.items():
            if key=='root': continue
            target=stage/(key+'_'+Path(value).name)
            working[key]=str(target)
            if Path(value).is_file(): shutil.copy2(value,target)
        if _ack_path(paths).is_file(): shutil.copy2(_ack_path(paths),_ack_path(working))
        yield working


def publish_dataset(working,paths,manifest):
    with DATA_LOCK:
        return _publish_dataset(working,paths,manifest)

def _publish_dataset(working,paths,manifest):
    root=Path(paths['root']);backup=root/'update_backups'/uuid.uuid4().hex
    backup.mkdir(parents=True)
    targets={key:Path(value) for key,value in paths.items() if key!='root' and Path(working[key]).is_file()}
    manifest_path=root/'dataset_manifest.json'
    staged_manifest=Path(working['root'])/'dataset_manifest.json'
    staged_manifest.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    sources={key:Path(working[key]) for key in targets}
    targets['manifest']=manifest_path;sources['manifest']=staged_manifest
    existed={};published=[]
    for key,target in targets.items():
        existed[key]=target.exists()
        if target.exists(): shutil.copy2(target,backup/key)
    pending=root/'.publish_pending.json'
    pending.write_text(dumps(dict(backup=str(backup.relative_to(root)),existed=existed,keys=list(targets))),encoding='utf-8')
    try:
        for key,target in targets.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            sources[key].replace(target);published.append(key)
            from .runtime_cache import clear_small_caches
            clear_small_caches();_CACHE.clear()
    except Exception:
        for key in reversed(published):
            if existed[key]: shutil.copy2(backup/key,targets[key])
            else: targets[key].unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
        audit_event(root,'INCREMENTAL_UPDATE_ROLLED_BACK',manifest.get('universe'),backup=str(backup.relative_to(root)))
        raise
    pending.unlink(missing_ok=True)
    audit_event(root,'INCREMENTAL_UPDATE_COMMITTED',manifest.get('universe'),dataset_id=manifest.get('dataset_id'),backup=str(backup.relative_to(root)))
    return str(backup.relative_to(root))

def recover_publication(paths):
    """Recover an interrupted multi-file publish before starting the HTTP server."""
    with DATA_LOCK:
        root=Path(paths['root']);pending=root/'.publish_pending.json'
        if not pending.exists(): return False
        state=json.loads(pending.read_text(encoding='utf-8'))
        backup=(root/state['backup']).resolve()
        if not backup.is_relative_to((root/'update_backups').resolve()): raise ValueError('Invalid recovery backup path')
        for key in state['keys']:
            target=root/'dataset_manifest.json' if key=='manifest' else Path(paths[key])
            if state['existed'][key]: shutil.copy2(backup/key,target)
            else: target.unlink(missing_ok=True)
        pending.unlink()
        audit_event(root,'INCREMENTAL_UPDATE_ROLLED_BACK',root.name,message='Recovered interrupted publication on startup')
        return True

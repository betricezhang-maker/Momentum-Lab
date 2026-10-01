"""Local calendar coverage evidence and the shared contiguous-signal gate."""
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
from .dates import normalize_dates
import hashlib
from .serialization import dumps


def evidence_paths(paths):
    root = Path(paths['root'])
    return {key: Path(paths.get(key, root / name)) for key, name in (
        ('lifecycle_csv', 'securities.csv'), ('suspensions_csv', 'suspensions.csv'),
        ('classification_csv', 'fund_classification.csv'))}


def open_sessions(calendar):
    if isinstance(calendar, pd.DataFrame):
        calendar = calendar.loc[pd.to_numeric(calendar.is_open, errors='coerce').eq(1), 'trade_date']
    return pd.DatetimeIndex(normalize_dates(pd.Series(list(calendar))).dropna().unique()).sort_values()


def gate_signal_windows(frame, lookback, calendar):
    """Mask bridged/invalid windows without changing complete-data arithmetic."""
    if calendar is None:
        raise ValueError('Exchange calendar required for MOM window validation.')
    sessions = open_sessions(calendar)
    x = frame.copy()
    ordinal = pd.Series(sessions.get_indexer(x.trade_date), index=x.index)
    groups = x.ts_code
    previous = ordinal.groupby(groups).shift()
    adjacent = ordinal.ge(0) & previous.ge(0) & ordinal.sub(previous).eq(1)
    x.loc[~adjacent, 'adj_daily_return_calc'] = np.nan
    good = pd.Series(np.isfinite(x.adj_close) & x.adj_close.gt(0) & ordinal.ge(0), index=x.index)
    count = good.groupby(groups).rolling(lookback+1, min_periods=lookback+1).sum().reset_index(level=0, drop=True)
    # Requiring every step to be adjacent also rejects duplicate daily keys.
    steps = adjacent.groupby(groups).rolling(lookback, min_periods=lookback).sum().reset_index(level=0, drop=True)
    valid = count.eq(lookback+1) & steps.eq(lookback)
    x['signal_window_valid'] = valid
    x['signal_exclusion_reason'] = np.where(valid, '', 'incomplete exchange-session window or invalid price')
    x.loc[ordinal.lt(0), 'signal_exclusion_reason'] = 'price date outside open exchange calendar'
    x.loc[ordinal.lt(lookback) & ordinal.ge(0), 'signal_exclusion_reason'] = 'insufficient exchange calendar warm-up'
    x.loc[~valid, ['signal_return', 'signal_daily_std', 'signal_volatility', 'momentum_score']] = np.nan
    return x


def assess_completeness(frames, paths, through=None, lookback=None):
    """Assess internal exchange-session coverage within stored raw-price bounds.

    In the absence of lifecycle evidence, an observed quote establishes existence
    from that date, not a listing date. Earlier missing history remains unknown.
    Missing suspension evidence never excuses a hole in this established scope.
    """
    findings = []
    def add(ticker, start, end, component, classification, severity, source, impact, count=1):
        findings.append(dict(ticker=ticker, date=start, end_date=end, affected_component=component,
                             classification=classification, severity=severity, evidence_source=source,
                             signal_impact=impact, missing_sessions=count))
    required = ('calendar', 'raw_price', 'adjusted_price', 'adjustment_factor', 'eligibility')
    if any(k not in frames or frames[k].empty for k in required):
        return dict(status='WARNING', findings=[], examples=[], counts={}, scope={},
                    limitations=['Required components unreadable; completeness cannot be established.'])
    cal, raw, adj, fac, weights = (frames[k] for k in required)
    sessions = open_sessions(cal)
    if sessions.empty:
        return dict(status='FAIL', findings=[], examples=[], counts={}, scope={}, limitations=['No open calendar sessions.'])
    width = int(lookback or 120)
    end = pd.Timestamp(through) if through is not None else normalize_dates(raw.trade_date).max()
    end = min(end.normalize(), pd.Timestamp.today().normalize())
    start = normalize_dates(raw.trade_date).min()
    scope_days = sessions[(sessions >= start) & (sessions <= end)]
    days = sessions[sessions <= end].strftime('%Y%m%d').tolist()
    if not days:
        return dict(status='WARNING', findings=[], examples=[], counts={}, scope={}, limitations=['No sessions in requested scope.'])
    day_array = np.array(days)
    sources = evidence_paths(paths)
    lifecycle, suspensions, limitations = {}, {}, []
    # The dedicated file overrides dated fields from classification, if present.
    for key in ('classification_csv', 'lifecycle_csv'):
        p = sources[key]
        if not p.is_file(): continue
        try:
            candidate = {code: dict(value) for code, value in lifecycle.items()}
            metadata = pd.read_csv(p, dtype=str).fillna('')
            if 'ts_code' not in metadata: raise ValueError('missing ts_code')
            for row in metadata.to_dict('records'):
                code = row['ts_code'].strip().upper()
                values = {}
                for field in ('list_date', 'delist_date'):
                    value = row.get(field, '').strip()
                    if value:
                        parsed = normalize_dates(pd.Series([value])).iloc[0]
                        if pd.isna(parsed): raise ValueError('invalid lifecycle date')
                        values[field] = parsed.strftime('%Y%m%d')
                if values:
                    candidate.setdefault(code, {}).update(values)
                    life = candidate[code]
                    if life.get('list_date', '') > life.get('delist_date', '99991231'):
                        raise ValueError('listing date after delisting date')
                    life['source'] = str(p)
            lifecycle = candidate
        except (ValueError, OSError) as exc:
            limitations.append(f'{p.name}: unusable lifecycle evidence: {exc}')
    p = sources['suspensions_csv']
    if p.is_file():
        try:
            metadata = pd.read_csv(p, dtype=str).fillna('')
            if not {'ts_code','start_date','end_date','full_session','source'}.issubset(metadata):
                raise ValueError('requires ts_code,start_date,end_date,full_session,source')
            for row in metadata.to_dict('records'):
                if row['full_session'].lower() not in ('1','true') or not row['source'].strip():
                    raise ValueError('only documented full-session suspensions are accepted')
                bounds = normalize_dates(pd.Series([row['start_date'],row['end_date']]))
                if bounds.isna().any() or bounds.iloc[0] > bounds.iloc[1]: raise ValueError('invalid suspension dates')
                suspensions.setdefault(row['ts_code'].strip().upper(), []).append(
                    (bounds.iloc[0].strftime('%Y%m%d'),bounds.iloc[1].strftime('%Y%m%d'),str(p)+': '+row['source']))
        except (ValueError, OSError) as exc:
            suspensions = {}
            limitations.append(f'{p.name}: unusable suspension evidence: {exc}')
    else:
        limitations.append('Suspension/status evidence unavailable; missing prices are never presumed suspended.')
    if not lifecycle: limitations.append('Listing/delisting evidence unavailable; pre-first-quote coverage is uncertain.')
    # Completeness scope is deliberately bounded by each ticker's stored raw history.
    # Lifecycle metadata and strategy lookback are not used to create expectations.
    snapshot_dates = sorted(weights.trade_date.unique())
    snapshot_next = dict(zip(snapshot_dates, snapshot_dates[1:]+['99991231']))
    present = {name: {str(t): set(g.trade_date) for t,g in frames[name].groupby('ts_code')}
               for name in ('raw_price','adjusted_price','adjustment_factor')}
    first_observed = raw.groupby('ts_code').trade_date.min().to_dict()
    last_observed = raw.groupby('ts_code').trade_date.max().to_dict()
    total_expected = 0
    for ticker, membership in weights.groupby('con_code', sort=True):
        needed = np.zeros(len(days), dtype=bool)
        observed_start, observed_end = first_observed.get(ticker), last_observed.get(ticker)
        if observed_start and observed_end:
            lo = int(np.searchsorted(day_array, observed_start))
            hi = int(np.searchsorted(day_array, observed_end, side='right'))
            if hi > lo: needed[lo:hi] = True
        life = lifecycle.get(ticker, {})
        observed = first_observed.get(ticker)
        expected_indices = np.flatnonzero(needed)
        total_expected += len(expected_indices)
        for component, by_ticker in present.items():
            available = by_ticker.get(ticker, set())
            buffer = None
            for index in expected_indices:
                day = days[index]
                if day in available:
                    if buffer: add(*buffer); buffer=None
                    continue
                if life.get('list_date') and day < life['list_date']:
                    kind, severity, source = 'BEFORE_LISTING', 'INFO', life['source']
                elif life.get('delist_date') and day > life['delist_date']:
                    kind, severity, source = 'AFTER_DELISTING', 'INFO', life['source']
                else:
                    suspension = next((src for a,b,src in suspensions.get(ticker,[]) if a<=day<=b), None)
                    if suspension and day not in present['raw_price'].get(ticker,set()):
                        kind, severity, source = 'CONFIRMED_SUSPENSION', 'INFO', suspension
                    else:
                        kind, severity = 'UNEXPLAINED_MISSING', 'FAIL'
                        source = 'internal exchange-session gap between first and last stored raw-price dates'
                impact = 'Exclude any MOM window containing this missing session.'
                if buffer and buffer[3:8] == [component,kind,severity,source,impact] and last_index+1==index:
                    buffer[2]=day;buffer[8]+=1
                else:
                    if buffer: add(*buffer)
                    buffer=[ticker,day,day,component,kind,severity,source,impact,1]
                last_index=index
            if buffer: add(*buffer)
    for limitation in limitations:
        add('*','','','evidence','EVIDENCE_LIMITATION','WARNING','unavailable',limitation,0)
    membership_dates={t:sorted(g.trade_date.unique()) for t,g in weights.groupby('con_code')}
    for row in findings:
        identity = '|'.join(str(row.get(k,'')) for k in ('ticker','date','end_date','affected_component','classification','evidence_source'))
        row['issue_id'] = 'CMP-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
        row['resolution_status'] = 'UNRESOLVED'
        row['blocks_research'] = row['severity'] == 'FAIL'
        row['overridable'] = row['affected_component'] in present and row['classification'] in ('UNEXPLAINED_MISSING','INSUFFICIENT_EVIDENCE','CONFIRMED_SUSPENSION')
        row['session_dates'] = [d for d in days if row['date'] and row['date'] <= d <= row['end_date']]
        ticker = row['ticker']
        row['raw_missing_dates'] = [d for d in row['session_dates'] if d not in present['raw_price'].get(ticker,set())]
        row['session_signatures'] = {}
        for day in row['session_dates']:
            # Only evidence relevant to this ticker/session belongs in the signature.
            # A later dataset date or a new unrelated snapshot must not revoke review.
            member_dates = membership_dates.get(ticker,[])
            anchor = max((d for d in member_dates if d <= day),default=next((d for d in member_dates if d > day),None))
            life = {k:v for k,v in lifecycle.get(ticker,{}).items() if k!='source'}
            status_evidence = [(a,b,src.split(': ',1)[-1]) for a,b,src in suspensions.get(ticker,[]) if a<=day<=b]
            row['session_signatures'][day] = hashlib.sha256(dumps(dict(ticker=ticker,day=day,component=row['affected_component'],
                classification=row['classification'],severity=row['severity'],lifecycle=life,status=status_evidence,
                eligibility_anchor=anchor,raw_missing=day in row['raw_missing_dates']),sort_keys=True).encode()).hexdigest()
    counts = Counter()
    for row in findings: counts[row['classification']] += max(row['missing_sessions'], 1)
    status = 'FAIL' if any(r['severity']=='FAIL' for r in findings) else 'WARNING' if any(r['severity']=='WARNING' for r in findings) else 'PASS'
    ordered=sorted(findings,key=lambda r:({'FAIL':0,'WARNING':1,'INFO':2}[r['severity']],r['ticker'],r['date']))
    return dict(status=status, counts=dict(counts), findings=ordered, examples=ordered[:30],
                scope=dict(start=str(sessions[0].date()), raw_start=str(start.date()), end=str(end.date()), lookback_sessions=width,
                           expected_ticker_sessions=total_expected, open_sessions=len(scope_days),
                           rule='Internal exchange sessions only: each ticker is checked from its first through last stored raw-price date. Leading/trailing omissions are not assessed; strategy lookback is handled by signal eligibility.',
                           suspension_rule='Only sourced, explicitly full-session date ranges explain missing quotes.'),
                limitations=limitations)


def apply_issue_resolutions(report, acknowledgements, frames=None):
    """Apply reviewed session evidence; preserve facts and split partial ranges."""
    import copy
    active = [a for a in acknowledgements if a.get('active',True) and str(a.get('explanation','')).strip()]
    raw_signatures = {(r['ticker'],d):s for r in report.get('findings',[]) if r['affected_component']=='raw_price'
                      for d,s in r.get('session_signatures',{}).items()}
    by_id = {a.get('confirmation_id'):a for a in active if a.get('confirmation_id')}
    output=[]
    for original in report.get('findings',[]):
        dates=original.get('session_dates') or ['']
        groups=[]
        for day in dates:
            candidates=[a for a in active if a.get('ticker')==original.get('ticker') and a.get('affected_component')==original.get('affected_component')
                        and (day in a.get('session_signatures',{}) or (a.get('issue_id')==original.get('issue_id') and not a.get('session_signatures')))]
            match=None
            for ack in reversed(candidates):
                valid = (ack.get('session_signatures',{}).get(day)==original.get('session_signatures',{}).get(day) if ack.get('session_signatures')
                         else ack.get('evidence_signature')==finding_signature(original))
                if ack.get('inherited_from'):
                    parent=by_id.get(ack['inherited_from'])
                    valid=valid and parent is not None and day in original.get('raw_missing_dates',[]) and parent.get('session_signatures',{}).get(day)==raw_signatures.get((original['ticker'],day))
                if valid and original.get('ticker')!='*' and original.get('overridable',False): match=ack;break
            status='CONFIRMED' if match else 'REVIEW_REQUIRED' if candidates else 'UNRESOLVED'
            if groups and groups[-1][0]==status and groups[-1][1]==match: groups[-1][2].append(day)
            else: groups.append([status,match,[day]])
        for status,ack,group_dates in groups:
            row=copy.deepcopy(original);row['resolution_status']=status
            row['blocks_research']=row.get('severity')=='FAIL' and status!='CONFIRMED'
            if group_dates!=dates:
                row.update(date=group_dates[0],end_date=group_dates[-1],session_dates=group_dates,missing_sessions=len(group_dates))
                row['session_signatures']={d:original['session_signatures'][d] for d in group_dates}
                row['raw_missing_dates']=[d for d in group_dates if d in original.get('raw_missing_dates',[])]
                row['issue_id']='CMP-'+hashlib.sha256((original['issue_id']+'|'+','.join(group_dates)).encode()).hexdigest()[:16]
            if ack:
                row.update(confirmation=copy.deepcopy(ack),explanation=ack['explanation'],confirmed_at=ack.get('confirmed_at',ack.get('timestamp')),
                           inherited_from=ack.get('inherited_from'),reopen_issue_id=ack.get('parent_issue_id',ack['issue_id']))
            output.append(row)
    report['findings']=output;report['examples']=output[:30]
    report['confirmation_history']=[]
    for ack in acknowledgements:
        item=copy.deepcopy(ack);dates=ack.get('session_dates',[]);component=ack.get('affected_component')
        resolved=[]
        if frames and component in frames and dates:
            found=set(frames[component].loc[frames[component].ts_code.eq(ack.get('ticker')),'trade_date'])
            resolved=[d for d in dates if d in found]
        item['resolved_dates']=resolved
        item['resolution_status']='RESOLVED' if dates and len(resolved)==len(dates) else 'REOPENED' if not ack.get('active',True) else 'CONFIRMED' if any(r.get('confirmation',{}).get('confirmation_id')==ack.get('confirmation_id') for r in output) else 'REVIEW_REQUIRED'
        report['confirmation_history'].append(item)
    report['acknowledged_count']=report['confirmed_count']=sum(r['resolution_status']=='CONFIRMED' for r in output)
    report['unresolved_blocking_count']=sum(bool(r.get('blocks_research')) for r in output)
    report['research_allowed_by_completeness']=report['unresolved_blocking_count']==0 and not (report.get('status')=='FAIL' and not output)
    report['research_permission_reason']='No unresolved blocking completeness findings.' if report['research_allowed_by_completeness'] else 'Unresolved blocking completeness findings remain.'
    return report


def finding_signature(finding):
    return hashlib.sha256(dumps({k: finding.get(k) for k in ('ticker','date','end_date','affected_component','classification','severity','evidence_source','signal_impact')}, sort_keys=True).encode()).hexdigest()

"""Read-only traces of retained production columns; no independent MOM calculator."""
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid
import zipfile
import numpy as np
import pandas as pd
from .live_model import select_target_rows, target_snapshot
from .reconstruction import research_bundle, compare_strategy_targets, saved_signal_target
from .serialization import sanitize_for_json, dumps
from .universe_validation import equity_classification
from .dates import normalize_dates


def saved_comparisons(root, strategy, day, reference, fingerprint):
    """Inspect exact saved rows, preserving run identity and mismatches across runs."""
    comparisons=[]
    for path in sorted((Path(root)/'backtests').glob('*/run_metadata.json')):
        try:
            meta=json.loads(path.read_text(encoding='utf-8'))
            if meta.get('universe')!=strategy['universe']: continue
            grid='rows' in meta
            rows=meta.get('rows',[]) if grid else [meta]
            files=[]
            for row in rows:
                if (row.get('lookback'),row.get('selection'),row.get('rebalance_days'))!=(strategy['lookback'],'Top 10',10): continue
                if row.get('error'): continue
                # Relocate portable run histories by basename within their owning run.
                name=str(row.get('history_file','')).replace('\\','/').split('/')[-1] if grid else 'rebalance_history.json'
                candidate=path.parent/name
                if name and candidate.is_file(): files.append(str(candidate))
            if not files: continue
            source=dict(run_id=meta.get('run_id',path.parent.name),history_files=files,provenance=meta.get('provenance'))
            candidate=saved_signal_target(source,day)
            if not candidate: continue
            match=compare_strategy_targets(reference,candidate)
            execution_match=reference.get('execution_date')==candidate.get('execution_date')
            saved_fp=candidate.get('provenance',{}).get('dataset_fingerprint')
            comparisons.append(dict(source='Strategy Grid' if grid else 'Research Lab',run_id=source['run_id'],
                                    status='MATCH' if match['match'] and execution_match else 'MISMATCH',
                                    dataset_fingerprint=saved_fp,same_dataset=bool(saved_fp and saved_fp==fingerprint),
                                    execution_match=execution_match,**match))
        except (ValueError,KeyError,OSError,TypeError) as exc:
            comparisons.append(dict(source='Saved run',run_id=path.parent.name,status='ERROR',reason=str(exc)))
    for label in ['Research Lab','Strategy Grid']:
        if not any(c['source']==label for c in comparisons):
            comparisons.append(dict(source=label,status='UNAVAILABLE',reason='No saved MOM / Top10 / 10D target on this exact signal date. No match inferred.'))
    return comparisons


def build_audit(market, strategy, day, ticker='', mode='Single Ticker Calculation', backtest=None, results_root=None):
    day=pd.Timestamp(day).normalize(); day_text=day.strftime('%Y-%m-%d'); n=int(strategy['lookback'])
    ranked=market['ranked']; calendar=market['calendar']
    if day_text not in calendar: raise ValueError('Select an exact open exchange signal date; audit dates are never rolled.')
    current=ranked[ranked.trade_date==day]
    if current.empty: raise ValueError('No production price observations on this signal date.')
    history=ranked[ranked.trade_date<=day]
    weights=market['weights'].copy(); weights['snapshot_date']=normalize_dates(weights.trade_date)
    snapshot=weights.loc[weights.snapshot_date<=day,'snapshot_date'].max()
    members=weights[weights.snapshot_date==snapshot].copy()
    members['con_code']=members.con_code.astype(str).str.strip()
    member_map={str(r['con_code']):r for r in members.to_dict('records')}
    groups={str(t):g for t,g in history.groupby('ts_code',sort=False)}
    metadata=market.get('classification',pd.DataFrame())
    categories={str(r['ts_code']):r.get('fund_type') for r in metadata.to_dict('records')} if 'ts_code' in metadata else {}
    equity=set(equity_classification(metadata).ts_code.astype(str)) if 'fund_type' in metadata else set()
    known=sorted(set(groups)|set(member_map))
    if ticker and ticker not in known: raise ValueError('Ticker is absent from the selected dataset and historical eligibility snapshot.')
    prices=[]; returns=[]; calculations=[]; exclusions=[]; traces={}
    required_dates=[d for d in calendar if d<=day_text][-(n+1):]
    for code in known:
        g=groups.get(code,pd.DataFrame())
        window=g[g.trade_date.isin(pd.to_datetime(required_dates))] if not g.empty else g
        on_date=not g.empty and g.iloc[-1].trade_date==day
        row=g.iloc[-1] if on_date else None
        eligible=bool(row.csi300_member==1) if row is not None else code in member_map
        reasons=[]
        if not eligible: reasons.append('not eligible')
        if not on_date: reasons.append('missing adjusted prices on signal date')
        if len(window)<n+1: reasons.append('insufficient history')
        if row is not None and row.get('signal_exclusion_reason'): reasons.append(row.signal_exclusion_reason)
        if not window.empty and window.adj_close.isna().any(): reasons.append('missing adjusted prices')
        score=row.momentum_score if row is not None else np.nan
        if row is not None and pd.isna(score):
            if row.signal_volatility==0: reasons.append('zero volatility')
            elif pd.isna(row.signal_volatility): reasons.append('invalid volatility / incomplete return window')
            else: reasons.append('invalid data / non-finite MOM score')
        price_rows=[]; return_rows=[]
        for j,(_,obs) in enumerate(window.iterrows()):
            price_rows.append(dict(ticker=code,observation=j,trade_date=obs.trade_date.strftime('%Y-%m-%d'),adjusted_close=obs.adj_close))
            if j:
                return_rows.append(dict(ticker=code,return_number=j,from_date=price_rows[j-1]['trade_date'],to_date=price_rows[j]['trade_date'],daily_return=obs.adj_daily_return_calc))
        span_start=price_rows[0]['trade_date'] if price_rows else day_text
        observed={r['trade_date'] for r in price_rows}
        missing=[d for d in required_dates if d not in observed]
        item=dict(ticker=code,name=market.get('names',{}).get(code,code),signal_date=day_text,
                  eligible=eligible,eligibility_snapshot_date=None if pd.isna(snapshot) else snapshot.strftime('%Y-%m-%d'),
                  eligibility_reason='Present in latest saved point-in-time membership snapshot' if eligible else 'Absent from latest saved point-in-time membership snapshot',
                  historical_size_rmb=member_map.get(code,{}).get('market_cap_rmb'),
                  size_threshold_rmb=250000000 if strategy['universe']=='ETF_250M' else None,
                  asset_class=categories.get(code),excluded_category_check=('PASS' if code in equity else 'FAIL') if code in categories else 'UNAVAILABLE',
                  classification_evidence='Undated classification file; not independent historical classification evidence',
                  price_observations=len(window),valid_price_observations=sum(pd.notna(r['adjusted_close']) for r in price_rows),
                  return_observations=sum(pd.notna(r['daily_return']) for r in return_rows),expected_prices=n+1,expected_returns=n,
                  missing_price_observations=max(0,n+1-sum(pd.notna(r['adjusted_close']) for r in price_rows)),
                  missing_return_observations=max(0,n-sum(pd.notna(r['daily_return']) for r in return_rows)),
                  missing_exchange_dates=missing,lookback_return=row.signal_return if row is not None else None,
                  daily_sd=row.signal_daily_std if row is not None else None,sqrt_n=float(np.sqrt(n)),
                  scaled_volatility=row.signal_volatility if row is not None else None,mom_score=score,
                  rank=row.momentum_rank if row is not None else None,percentile=row.momentum_percentile if row is not None else None,
                  top5=bool(row is not None and row.momentum_rank<=5),top10=bool(row is not None and row.momentum_rank<=10),
                  top20=bool(row is not None and row.momentum_rank<=20),exclusion_reasons=reasons)
        calculations.append(item); prices.extend(price_rows); returns.extend(return_rows)
        if reasons and (not eligible or pd.isna(score)): exclusions.append(dict(ticker=code,reasons='; '.join(reasons),missing_dates='; '.join(missing)))
        if code==ticker: traces[code]=dict(summary=item,price_inputs=price_rows,daily_returns=return_rows)
    valid=current[(current.csi300_member==1)&current.momentum_rank.notna()].sort_values('momentum_rank')
    ranking=[]
    for _,row in valid.iterrows():
        exact=valid[(valid.momentum_score==row.momentum_score)&(valid.ts_code!=row.ts_code)]
        near=valid[np.isclose(valid.momentum_score,row.momentum_score,rtol=1e-10,atol=1e-12)&(valid.ts_code!=row.ts_code)]
        ranking.append(dict(rank=row.momentum_rank,ticker=str(row.ts_code),name=market.get('names',{}).get(str(row.ts_code),str(row.ts_code)),
                            mom_score=row.momentum_score,percentile=row.momentum_percentile,eligible=True,top10=bool(row.momentum_rank<=10),
                            exact_ties=', '.join(exact.ts_code),near_ties=', '.join(near.ts_code)))
    chosen=select_target_rows(current,'Top 10',day)
    target=[dict(ticker=str(r.ts_code),rank=r.momentum_rank,mom_score=r.momentum_score,weight=1/len(chosen)) for _,r in chosen.iterrows()]
    i=bisect_right(calendar,day_text); execution=calendar[i] if i<len(calendar) else None
    reference=dict(weights={r['ticker']:r['weight'] for r in target},ranks={r['ticker']:r['rank'] for r in target},execution_date=execution)
    comparisons=[]
    try:
        preview=target_snapshot(strategy,market,day_text)
        match=compare_strategy_targets(reference,preview)
        comparisons.append(dict(source='Portfolio Preview (production target snapshot)',status='MATCH' if match['match'] and preview['execution_date']==execution else 'MISMATCH',**match))
    except ValueError as exc: comparisons.append(dict(source='Portfolio Preview',status='UNAVAILABLE',reason=str(exc)))
    if backtest:
        try:
            if not execution: raise ValueError('Future exchange calendar unavailable.')
            net,_,log,_=backtest(ranked,'Top 10',10,day,pd.Timestamp(execution),0)
            candidate=research_bundle(log,net,'audit-production-backtest')['signals'][0]
            if candidate['signal_date']!=day_text: raise ValueError('Production backtest has no target on this exact date.')
            match=compare_strategy_targets(reference,candidate)
            comparisons.append(dict(source='Backtest (production first rebalance)',status='MATCH' if match['match'] and candidate['execution_date']==execution else 'MISMATCH',execution_date=candidate['execution_date'],**match))
        except (ValueError,IndexError,KeyError) as exc: comparisons.append(dict(source='Backtest',status='UNAVAILABLE',reason=str(exc)))
    prov=market.get('provenance',{})
    if results_root: comparisons+=saved_comparisons(results_root,strategy,day_text,reference,prov.get('dataset_fingerprint'))
    manifest=dict(**prov,audit_id=uuid.uuid4().hex,audit_timestamp=datetime.now(timezone.utc).isoformat(),
                  momentum_lookback=n,selection='Top 10',rebalance_days=10,audit_mode=mode,ticker=ticker,
                  eligible_count=len(members),valid_score_count=len(valid),
                  research_run_ids=[c['run_id'] for c in comparisons if c['source']=='Research Lab' and 'run_id' in c],
                  strategy_run_ids=[c['run_id'] for c in comparisons if c['source']=='Strategy Grid' and 'run_id' in c])
    manifest.update(universe=strategy['universe'],signal_date=day_text,execution_date=execution,
                    source_components=market.get('source_components',{}),
                    formula='MOM_N = N-observation return / (SD of last N daily returns × sqrt(N)). This is not Sharpe Ratio.',
                    observation_rule='N+1 prices across exactly N consecutive open exchange sessions; missing/suspended sessions invalidate MOM; no price filling or gap bridging.',
                    sd_rule='Sample standard deviation, ddof=1, min_periods=N.',
                    rank_rule='Higher MOM = stronger momentum; descending rank(method=first) in production row order (ticker/date sorted). Near ties are diagnostic only; no rounding before rank.',
                    percentile_rule='Ascending rank(pct=True), default average rank for ties.',
                    weight_check='PASS' if target and abs(sum(r['weight'] for r in target)-1)<1e-12 else 'FAIL',
                    target_weight_rule='Equal weight 1 / actual selected count; fewer than 10 when fewer valid scores exist.',
                    execution_rule='Next open exchange session; production backtest comparison exposes any difference in its observed-price calendar.')
    return sanitize_for_json(dict(manifest=manifest,integrity=market.get('integrity',{}),trace=traces.get(ticker),
                                  universe_snapshot=members.drop(columns=['snapshot_date']).to_dict('records'),
                                  price_inputs=prices,daily_returns=returns,mom_calculation=calculations,ranking=ranking,
                                  top10_target=target,excluded_securities=exclusions,comparisons=comparisons))


def export_audit(audit, root):
    folder=Path(root)/'research_audits'/audit['manifest']['audit_id']; folder.mkdir(parents=True,exist_ok=False)
    (folder/'audit_manifest.json').write_text(dumps(dict(**audit['manifest'],integrity=audit['integrity'],comparisons=audit['comparisons']),indent=2,ensure_ascii=False),encoding='utf-8')
    for key in ['universe_snapshot','price_inputs','daily_returns','mom_calculation','ranking','top10_target','excluded_securities']:
        frame=pd.DataFrame(audit[key])
        if frame.empty: frame=pd.DataFrame(columns=['ticker','status'])
        frame.to_csv(folder/(key+'.csv'),index=False,float_format='%.17g',encoding='utf-8-sig')
    archive=folder.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as out:
        for file in folder.iterdir(): out.write(file,file.name)
    return str(archive)

"""Adapt existing research histories into immutable portfolio targets; no ranking engine."""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from .serialization import sanitize_for_json


def research_bundle(history, net, source):
    signals=[]
    for row in history.to_dict('records'):
        weights=json.loads(row['target_weights'])
        details=json.loads(row.get('target_details') or '[]')
        ordered=[str(item['ticker']) for item in details] or [t for t in str(row.get('tickers','')).split(',') if t]
        ordered+=[ticker for ticker in weights if ticker not in ordered]
        weights={ticker:weights[ticker] for ticker in ordered}
        ranks={item['ticker']:item.get('rank') for item in details}
        scores={item['ticker']:item.get('score') for item in details}
        if not ranks: ranks={ticker:index for index,ticker in enumerate(ordered,1)}
        signals.append(dict(signal_date=row['signal_date'],ranking_date=row['signal_date'],
                            execution_date=row['entry_date'],weights=weights,ranks=ranks,scores=scores,names={},
                            backfilled=True,data_end=str(net.index.max().date()),source='RESEARCH',source_run_id=source))
    return sanitize_for_json(dict(signals=signals,expected_nav={str(d.date()):float(v) for d,v in net.items()},source=source))


def compare_strategy_targets(reference, candidate, weight_tolerance=1e-12):
    reference_weights=reference.get('weights',{})
    candidate_weights=candidate.get('weights',{})
    shared=sorted(reference_weights.keys() & candidate_weights.keys())
    rank_mismatches=[]
    for ticker in shared:
        left=reference.get('ranks',{}).get(ticker)
        right=candidate.get('ranks',{}).get(ticker)
        if left is not None and right is not None and left!=right:
            rank_mismatches.append({'ticker':ticker,'reference':left,'candidate':right})
    weight_mismatches=[{'ticker':ticker,'reference':reference_weights[ticker],'candidate':candidate_weights[ticker]}
                       for ticker in shared if abs(reference_weights[ticker]-candidate_weights[ticker])>weight_tolerance]
    missing=sorted(reference_weights.keys()-candidate_weights.keys())
    extra=sorted(candidate_weights.keys()-reference_weights.keys())
    return dict(match=not missing and not extra and not rank_mismatches and not weight_mismatches,
                reference_count=len(reference_weights),candidate_count=len(candidate_weights),
                missing_from_candidate=missing,extra_in_candidate=extra,
                rank_mismatches=rank_mismatches,weight_mismatches=weight_mismatches)


def saved_signal_target(source,signal_date):
    """Read the exact dated target from verified run history; never infer a new cycle."""
    candidates=[]
    for filename in source.get('history_files',[]):
        rows=json.loads(Path(filename).read_text(encoding='utf-8'))
        for row in rows:
            if row.get('signal_date')!=signal_date: continue
            series=pd.Series([1.],index=pd.DatetimeIndex([row['entry_date']]))
            try: target=research_bundle(pd.DataFrame([row]),series,source['run_id'])['signals'][0]
            except (KeyError,ValueError,TypeError) as exc:
                raise ValueError(f'STRATEGY TARGET MISMATCH: saved target fields are inconsistent ({exc}).') from exc
            target['provenance']=row.get('provenance') or source.get('provenance') or {}
            candidates.append(target)
    if not candidates:return None
    first=candidates[0]
    for other in candidates[1:]:
        if other['execution_date']!=first['execution_date'] or not compare_strategy_targets(first,other)['match']:
            raise ValueError('STRATEGY TARGET MISMATCH: saved histories disagree for the same signal date.')
    return first


def reconcile(rows, expected, capital):
    observed={r['date']:r['model_nav'] for r in rows}
    common=sorted(set(expected)&set(observed))
    if not common:
        return dict(status='UNAVAILABLE',difference=None,backtest_ending_nav=None,reconstructed_ending_nav=None)
    last=common[-1]
    difference=observed[last]-expected[last]*capital
    maximum=max(abs(observed[d]-expected[d]*capital) for d in common)
    matches=len(common)==len(expected) and np.isfinite(maximum) and maximum<=1e-8*max(capital,1)
    return dict(status='MATCH' if matches else 'MISMATCH',difference=difference,max_absolute_difference=maximum,
                backtest_ending_nav=expected[last]*capital,reconstructed_ending_nav=observed[last],
                observations_compared=len(common),asof=last,tolerance=1e-8*max(capital,1))

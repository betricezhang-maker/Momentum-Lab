"""Compare measured before/after artifacts; ignores identities, never numerical fields."""
import ast
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

root=Path(__file__).resolve().parents[1];before=root/'results/performance/before';after=root/'results/performance'/sys.argv[1]
def load(folder,name):return json.loads((folder/(name+'.json')).read_text(encoding='utf-8'))
def equal(a,b,path='root'):
    if isinstance(a,dict):
        assert a.keys()==b.keys(),path
        for k in a:equal(a[k],b[k],path+'.'+k)
    elif isinstance(a,list):
        assert len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)):equal(x,y,f'{path}[{i}]')
    elif isinstance(a,(int,float)) and not isinstance(a,bool):
        assert np.isclose(a,b,rtol=1e-12,atol=1e-12,equal_nan=True),(path,a,b)
    else:assert a==b,(path,a,b)
def clean_history(rows):return [{k:v for k,v in r.items() if k!='provenance'} for r in rows]

completed=[]
for path in sorted(after.glob('grid_*.json')):
    baseline=load(before,'grid_1');current=json.loads(path.read_text(encoding='utf-8'))
    assert baseline['provenance']['dataset_fingerprint']==current['provenance']['dataset_fingerprint']
    ignored={'history_file','strategy_run_id'}
    equal([{k:v for k,v in r.items() if k not in ignored} for r in baseline['rows']],
          [{k:v for k,v in r.items() if k not in ignored} for r in current['rows']])
    for left,right in zip(baseline['rows'],current['rows']):
        a=json.loads(Path(left['history_file']).read_text());b=json.loads(Path(right['history_file']).read_text())
        equal(clean_history(a),clean_history(b))
        nav_a=Path(left['history_file'].replace('_rebalances.json','_net_nav.csv'))
        nav_b=Path(right['history_file'].replace('_rebalances.json','_net_nav.csv'))
        assert_frame_equal(pd.read_csv(nav_a),pd.read_csv(nav_b),rtol=1e-12,atol=1e-12)
    completed.append(path.stem)
for path in sorted(after.glob('research_*.json')):
    baseline=load(before,'research_1');current=json.loads(path.read_text(encoding='utf-8'))
    assert baseline['provenance']['dataset_fingerprint']==current['provenance']['dataset_fingerprint']
    for key in ['stats','decay','lookback_comparison','comparison_counts','decay_counts','top20','snapshots','notes']:
        equal(baseline[key],current[key],key)
    equal(clean_history(baseline['rebalance_history']),clean_history(current['rebalance_history']))
    # Compare every numeric research CSV (NAV, drawdowns, metrics and diagnostic matrices).
    old_folder=Path(baseline['files']['equity']).parent if 'equity' in baseline['files'] else Path(next(iter(baseline['files'].values()))).parent
    new_folder=Path(current['files']['equity']).parent if 'equity' in current['files'] else Path(next(iter(current['files'].values()))).parent
    for csv in old_folder.glob('*.csv'):
        assert_frame_equal(pd.read_csv(csv),pd.read_csv(new_folder/csv.name),rtol=1e-12,atol=1e-12)
    completed.append(path.stem)
if (after/'audit.json').exists():
    a=load(before,'audit');b=load(after,'audit')
    for key in ['trace','ranking','mom_calculation','top10_target','universe_snapshot','excluded_securities','comparisons']:
        equal(a[key],b[key],key)
    assert a['manifest']['dataset_fingerprint']==b['manifest']['dataset_fingerprint']
    completed.append('audit')
if (after/'live_detail.json').exists():
    a=load(before,'live_detail');b=load(after,'live_detail')
    for key in ['stats','nav','positions','position_history','model_journals','rebalance_workflow']:
        equal(a[key],b[key],key)
    completed.append('live_detail')
old=ast.parse((before/'MomentumLabV2.baseline.py').read_text(encoding='utf-8'))
new=ast.parse((root/'MomentumLabV2.py').read_text(encoding='utf-8'))
for name in ['calculate_signal','merge_membership_and_rank','backtest','backtest_v4','select_mask','signal_decay_table','selection_forward_returns']:
    a=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==name)
    b=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name==name)
    assert ast.dump(a)==ast.dump(b),name
print('PASS: same fingerprints, numerical outputs within 1e-12, unchanged core math AST:',', '.join(completed))

"""Reproducible local benchmark. Writes only isolated artifacts and a copied live DB.
Run with native app Python: tests/profile_workflows.py before|after [repeat-count].
"""
import collections
import copy
import cProfile
import ctypes
import gc
import json
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import threading
import time
import weakref

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
label=sys.argv[1];repeats=int(sys.argv[2]) if len(sys.argv)>2 else 1
output=ROOT/'results'/'performance'/label;output.mkdir(parents=True,exist_ok=True)
class Memory(ctypes.Structure):
    _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(k,ctypes.c_size_t) for k in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Memory),ctypes.c_ulong]
def rss():
    m=Memory();m.cb=ctypes.sizeof(m)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(m),m.cb)
    return m.WorkingSetSize/1048576
start=time.perf_counter();import MomentumLabV2 as app
import pandas as pd
import momentumlab.data_integrity as integrity
report=dict(label=label,startup_seconds=time.perf_counter()-start,idle_mb=rss(),actions=[],settings={})
cfg=app.load_config();cfg['active_universe']='ETF_250M';cfg['results_folder']=str(output/'artifacts')
app.load_config=lambda:copy.deepcopy(cfg);app.set_active_universe=lambda u:None
# Keep validation semantics, but do not append profiling activity to production audit logs.
original_audit=integrity.audit_event
counts=collections.Counter();reads=collections.Counter();frames=[];seen=set()
def audit_event(root,event,universe,**kw):
    if event=='DATA_VALIDATION_STARTED':counts['full_integrity_scans']+=1
integrity.audit_event=audit_event
def remember(value,source):
    if isinstance(value,pd.DataFrame) and (source,value.shape) not in seen:
        seen.add((source,value.shape));frames.append(dict(source=source,shape=value.shape,deep_mb=float(value.memory_usage(deep=True).sum()/1048576),dtypes={str(k):str(v) for k,v in value.dtypes.items()}))
def instrument(name):
    original=getattr(app,name)
    def run(*a,**kw):
        counts[name]+=1;value=original(*a,**kw);remember(value,name);return value
    setattr(app,name,run)
for name in ['calculate_signal','merge_membership_and_rank','validate_research_files','backtest_v4']:
    instrument(name)
original_read=pd.read_csv
def read(path,*a,**kw):
    reads[str(path)]+=1;v=original_read(path,*a,**kw);remember(v,'CSV '+Path(str(path)).name);return v
pd.read_csv=read
def retained():
    found=[]
    for key,value in app._LIVE_MARKET_CACHE.items():
        for name,obj in value[1].items():
            if isinstance(obj,pd.DataFrame):found.append(dict(cache='live_market',name=name,mb=float(obj.memory_usage(deep=True).sum()/1048576)))
    return found
def action(name,fn):
    counts.clear();reads.clear();frames.clear();seen.clear();peak=[rss()];stop=threading.Event()
    def sample():
        while not stop.wait(.05):peak[0]=max(peak[0],rss())
    thread=threading.Thread(target=sample);thread.start();profile=cProfile.Profile();before=rss();t=time.perf_counter()
    try:
        profile.enable();value=fn();profile.disable()
        elapsed=time.perf_counter()-t;peak[0]=max(peak[0],rss());after=rss()
        stats=pstats.Stats(profile)
        slow=sorted([(f'{Path(k[0]).name}:{k[1]}:{k[2]}',v[1],v[3]) for k,v in stats.stats.items() if 'MomentumLab' in k[0] or 'momentumlab' in k[0]],key=lambda x:x[2],reverse=True)[:15]
        entry=dict(name=name,seconds=elapsed,start_mb=before,peak_mb=peak[0],end_mb=after,counts=dict(counts),csv_reads=dict(reads),largest_frames=sorted(frames,key=lambda x:x['deep_mb'],reverse=True)[:8],slowest=slow,retained=retained())
        report['actions'].append(entry)
        (output/(name+'.json')).write_text(app.json_dumps(value),encoding='utf-8')
        del value;gc.collect();entry['post_gc_mb']=rss()
        (output/'profile.json').write_text(app.json_dumps(report,indent=2),encoding='utf-8')
        print(app.json_dumps({k:v for k,v in entry.items() if k in ('name','seconds','peak_mb','end_mb','counts','post_gc_mb')}),flush=True)
    finally:stop.set();thread.join()

strategy=dict(universe='ETF_250M',lookbacks=[10,20],rebalances=[10],selections=['Top 5','Top 10'],years=[2026],include_full=False,trading_cost_bps=5)
research=dict(universe='ETF_250M',lookback=10,display_spans=[10,20],selection='Top 10',rebalance_days=10,start_date='2026-01-01',end_date='2026-09-04',trading_cost_bps=5)
report['settings']=dict(grid=strategy,research=research)
action('data_manager',app.data_status)
action('integrity_cold',lambda:app.validate_research_files('ETF_250M',mode='full'))
action('integrity_warm',lambda:app.validate_research_files('ETF_250M'))
for i in range(repeats):
    action(f'grid_{i+1}',lambda:app.run_strategy_grid(strategy))
    action(f'research_{i+1}',lambda:app.run_research(research))
source=sqlite3.connect(f'file:{ROOT / "live" / "momentumlab.sqlite3"}?mode=ro',uri=True)
dest=sqlite3.connect(output/'profile_live.sqlite3');source.backup(dest);dest.close();source.close()
store=app.LiveStore(output/'profile_live.sqlite3')
service=app.LiveService(store,app.live_market,app.historical_reconstruction,app.historical_target_preview)
portfolios=store.list_portfolios()
if portfolios:action('live_detail',lambda:service.detail(portfolios[0]['id']))
action('audit',lambda:app.run_research_audit(dict(universe='ETF_250M',signal_date='2026-09-04',lookback=10,ticker='159208.SZ',mode='Full Signal Audit Package')))
action('data_manager_warm',app.data_status)
print('PROFILE COMPLETE '+str(output/'profile.json'),flush=True)

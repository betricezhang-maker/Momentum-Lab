const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements={};
const defaults={liveStart:'2026-09-05',liveStartDateType:'SIGNAL',liveEnd:'2026-09-14',liveCost:'5',liveCapital:'',liveName:'Pilot',liveCreateStatus:'PAPER',liveStartMode:'historical'};
for(const id of [...Object.keys(defaults),'historicalPreview','createLiveReasons','createLiveSubmit','previewHistoricalBtn','historicalEndBox','liveStartLabel'])elements[id]={value:defaults[id]||'',textContent:'',innerHTML:'',disabled:false};
let resolveFetch,timer;
const context=vm.createContext({console,AbortController,$:id=>elements[id],
 escapeHtml:v=>String(v??'').replaceAll('<','&lt;'),pct:v=>String(v*100)+'%',
 setTimeout:cb=>{timer=cb;return 1;},clearTimeout:()=>{},
 fetch:()=>new Promise(resolve=>{resolveFetch=resolve;})});
vm.runInContext("let pendingLiveSource={run_id:'known',lookback:10,selection:'Top 10',rebalance_days:10};",context);
vm.runInContext(fs.readFileSync('ui/live-portfolio.js','utf8'),context);
const ready={ok:true,preview_status:'READY',signal_date:'2026-09-04',execution_date:'2026-09-07',requested_start_date:'2026-09-05',
 strategy:{lookback:10,selection:'Top 10',rebalance_days:10,cost_bps:5},dataset_integrity:{status:'WARNING'},
 integrity:{research_vs_backtest:{match:true},saved_grid_vs_backtest:{match:true},target_source:'SAVED_STRATEGY_GRID_TARGET'},holdings:Array.from({length:10},(_,i)=>({ticker:'ETF'+i,rank:i+1,name:'Name',score:.5,target_weight:.1}))};
(async()=>{
 const pending=vm.runInContext('previewHistoricalTarget()',context);
 assert(elements.createLiveSubmit.disabled);
 resolveFetch({ok:true,json:async()=>ready});await pending;
 assert.match(elements.historicalPreview.innerHTML,/2026-09-04/);
 assert.match(elements.historicalPreview.innerHTML,/2026-09-07/);
 assert.match(elements.historicalPreview.innerHTML,/MOM score/);
 assert(elements.createLiveSubmit.disabled);
 assert.match(elements.createLiveReasons.textContent,/starting capital/);
 elements.liveCapital.value='10000';vm.runInContext('updateCreateLiveState()',context);
 assert.equal(elements.createLiveSubmit.disabled,false);
 elements.liveName.value='';vm.runInContext('updateCreateLiveState()',context);assert(elements.createLiveSubmit.disabled);
 elements.liveName.value='Pilot';
 const stale=vm.runInContext('previewHistoricalTarget()',context);
 elements.liveStart.value='2026-09-08';vm.runInContext('invalidateHistoricalPreview()',context);
 resolveFetch({ok:true,json:async()=>ready});await stale;assert(elements.createLiveSubmit.disabled);
 const timeout=vm.runInContext('previewHistoricalTarget()',context);timer();await timeout;
 assert.match(elements.historicalPreview.textContent,/ERROR.*timed out/);assert(elements.createLiveSubmit.disabled);
 const failed=vm.runInContext('previewHistoricalTarget()',context);
 resolveFetch({ok:false,json:async()=>({ok:false,preview_status:'DATASET BLOCKED',error:'Missing prices'})});await failed;
 assert.match(elements.historicalPreview.textContent,/DATASET BLOCKED.*Missing prices/);
 assert.equal(elements.previewHistoricalBtn.disabled,false);
 console.log('PASS: Preview READY, warning, capital/name gates, stale responses, timeout, dataset failure and retry.');
})().catch(e=>{console.error(e);process.exitCode=1;});

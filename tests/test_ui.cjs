const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../ui/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Check syntax of the entire UI, including event handlers.
const elements = {};
for (const id of ['gridPeriod','gridMetric','gridSelection','gridMeta','gridSummary','gridMatrix']) {
  elements[id] = {value:'',innerHTML:'',selectedOptions:[{textContent:'Total Return'}]};
}
elements.gridPeriod.value='2024'; elements.gridMetric.value='total_return'; elements.gridSelection.value='Q5';
const context=vm.createContext({document:{getElementById:id=>elements[id]},console});
vm.runInContext('const $=id=>document.getElementById(id);\n'+
  script.slice(script.indexOf('function escapeHtml('),script.indexOf('\n',script.indexOf('function escapeHtml(')))+'\n'+
  script.slice(script.indexOf('let strategyGridData='),script.indexOf('function drawMiniTrend')),context);
const data={universe_label:'Test Universe',dataset_start:'2024-01-01',dataset_end:'2025-12-31',
  trading_cost_bps:5,selection_rules:['Top 5','Q5'],lookbacks:[10],rebalances:[5],periods:['2024','2025','Full Period'],rows:[]};
for(const selection of data.selection_rules)for(const period of data.periods){
  data.rows.push({selection,period,lookback:10,rebalance_days:5,total_return:selection==='Q5'?.42:.11,
    sharpe_rf0:1,max_drawdown:-.2,backtest_start:'2024-01-02',backtest_end:'2024-12-31',observations:250,rebalances:50});
}
context.testData=data;
const originalOutput=JSON.stringify(data);
vm.runInContext('strategyGridData=testData; renderStrategyGrid()',context);
assert.equal(JSON.stringify(data),originalOutput,'Visualization must not mutate API/export data');
assert.match(elements.gridSummary.innerHTML,/Quality \/ 100/);
assert.match(elements.gridSummary.innerHTML,/78 \/ 100/);
assert.match(elements.gridSummary.innerHTML,/display-only heuristic/);
assert.match(elements.gridSummary.innerHTML,/Top 5 \| MOM10/);
assert.match(elements.gridSummary.innerHTML,/Q5 \| MOM10/);
assert.match(elements.gridMatrix.innerHTML,/42.00%/);
assert.doesNotMatch(elements.gridMatrix.innerHTML,/11.00%/);
assert.match(elements.gridMatrix.innerHTML,/actual 2024-01-02/);
data.rows.find(r=>r.selection==='Q5'&&r.period==='2025').error='missing coverage <unsafe>';
vm.runInContext('renderStrategyGrid()',context);
assert.match(elements.gridSummary.innerHTML,/FAILED/);
assert.match(elements.gridSummary.innerHTML,/missing coverage &lt;unsafe&gt;/);
assert.match(elements.gridSummary.innerHTML,/1\/2/);
assert.equal(vm.runInContext("gridMetricText('sharpe_rf0',null)",context),'—');
assert.match(vm.runInContext("gridCellStyle('annualized_volatility',.1,[.1,.5])",context),/10,124,73/);
assert.match(vm.runInContext('returnStyle(-.1,.5)',context),/180,35,24/);
assert.match(vm.runInContext('returnStyle(.1,.5)',context),/10,124,73/);
assert.match(vm.runInContext('returnStyle(0,.5)',context),/#f8fafc/);
assert.equal(vm.runInContext('returnStyle(null,.5)',context),'');
assert.match(vm.runInContext("gridCellStyle('total_return',.1,[.1,.5])",context),/10,124,73/,'Small positive returns must not look negative');
assert.match(vm.runInContext('drawdownStyle(-.10)',context),/10,124,73/);
assert.match(vm.runInContext('drawdownStyle(-.25)',context),/166,99,0/);
assert.match(vm.runInContext('drawdownStyle(-.50)',context),/180,35,24/);
assert.match(vm.runInContext('consistencyPill(2,3,3)',context),/High/);
assert.match(vm.runInContext('consistencyPill(1,2,2)',context),/Mixed/);
assert.match(vm.runInContext('consistencyPill(0,2,2)',context),/Low/);
assert.match(vm.runInContext('consistencyPill(1,3,1)',context),/Limited/);
assert.equal(vm.runInContext('strategyQuality({returns:[1,1],positive:2,full:{sharpe_rf0:2,max_drawdown:0}},2)',context),100);
assert.equal(vm.runInContext('strategyQuality({returns:[-1,-1],positive:0,full:{sharpe_rf0:-1,max_drawdown:-.8}},2)',context),0);
assert.equal(vm.runInContext('strategyQuality({returns:[1],positive:1,full:{sharpe_rf0:2,max_drawdown:0}},1)',context),null);
assert.equal(vm.runInContext('strategyQuality({returns:[1,1],positive:2,full:{sharpe_rf0:null,max_drawdown:0}},2)',context),null);
assert.equal(vm.runInContext('strategyQuality({returns:[1],positive:1,full:{sharpe_rf0:2,max_drawdown:0}},2)',context),null);
console.log('PASS: UI syntax; portfolio identity, failed years, coverage labels, escaping, null metrics, volatility colors.');
console.log('PASS: zero-centered returns; reliability/risk thresholds; bounded display-only quality; input data unchanged.');

// Tab initialization must retain loading actions after assigning onclick handlers.
let liveLoads=0,auditLoads=0;
const tabs=['live','audit'].map(page=>({dataset:{page},classList:{add(){},remove(){}}}));
const pages=tabs.map(()=>({classList:{add(){},remove(){}}}));
const tabContext=vm.createContext({document:{querySelectorAll:q=>q==='.tab'?tabs:pages},$:()=>pages[0],loadLivePortfolios:()=>liveLoads++,loadLiveAudit:()=>auditLoads++});
vm.runInContext(script.split('\n').find(line=>line.startsWith("document.querySelectorAll('.tab')")),tabContext);
tabs[0].onclick();tabs[1].onclick();
assert.equal(liveLoads,1);assert.equal(auditLoads,1);
for(const file of ['strategy-grid.js','live-portfolio.js'])new vm.Script(fs.readFileSync(path.join(__dirname,'../ui',file),'utf8'));
const liveScript=fs.readFileSync(path.join(__dirname,'../ui/live-portfolio.js'),'utf8');
assert.match(html,/id="rebalanceDialog"/);
assert.match(liveScript,/NEXT REBALANCE/);
assert.match(liveScript,/Apply Paper Rebalance/);
assert.match(liveScript,/PARTIALLY_EXECUTED/);
assert.match(liveScript,/Suggested Trade Value \(RMB\)/);
assert.match(liveScript,/Proposed Rebalanced Portfolio/);
assert.match(liveScript,/Final Portfolio After Rebalance/);
assert.match(liveScript,/Final Target Value \(RMB\).*Status/);
assert.match(liveScript,/External Contributions/);
assert.match(html,/id="deletePortfolioConfirmation"/);
assert.match(html,/id="liveView"/);
console.log('PASS: Live/Audit tab loading hooks and V4 external JavaScript syntax.');

// Exercise the complete Live view, including derived proposal fields and lifecycle controls.
const liveElements={liveMessage:{textContent:''},liveContent:{innerHTML:''}};
const liveContext=vm.createContext({$:id=>liveElements[id],console,
  escapeHtml:v=>String(v??'').replaceAll('<','&lt;'),pct:v=>v==null?'—':(v*100).toFixed(2)+'%',
  universeLabel:()=> 'Equity ETF ≥ RMB250M'});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/pagination.js'),'utf8'),liveContext);
vm.runInContext(liveScript,liveContext);
vm.runInContext('drawLiveChart=()=>{};loadLiveAudit=()=>{};',liveContext);
liveContext.fixture={portfolio:{id:'test',name:'Portfolio <sample>',mode:'ACTIVE',status:'ACTIVE',capital:10000,start_date:'2026-09-07',strategy:{lookback:10,selection:'Top 10',rebalance_days:10,cost_bps:5}},
  stats:{current_nav:10520,cash:0},asof:'2026-09-14',data_end:'2026-09-14',signals:[],positions:[],journals:[],trades:[],cash_flows:[],nav:[],notes:[],
  reconciliation:{status:'UNAVAILABLE'},rebalance_workflow:{status:'WAITING_FOR_EXECUTION',strategy:'MOM10 / Top 10 / 10D',current_nav:10520,investable_nav:12520,capital_added:2000,capital_withdrawn:0,
    recommendations:[{ticker:'A',name:'ETF A',action:'CARRY / INCREASE',status:'CARRIED',current_value:980,target_value:1252,suggested_trade_value:272,final_target_value:1252}],
    final_portfolio:[{ticker:'A',name:'ETF A',status:'CARRIED',final_target_value:1252}]}};
vm.runInContext('liveDetail=fixture;renderLive()',liveContext);
assert.match(liveElements.liveContent.innerHTML,/Next Signal Date:/);
assert.match(liveElements.liveContent.innerHTML,/Target Status:/);
assert.doesNotMatch(liveElements.liveContent.innerHTML,/Awaiting calendar|Not generated/);
assert.match(liveElements.liveContent.innerHTML,/CARRY \/ INCREASE/);
assert.match(liveElements.liveContent.innerHTML,/12,520\.00/);
assert.match(liveElements.liveContent.innerHTML,/Portfolio &lt;sample>/);
assert.match(liveElements.liveContent.innerHTML,/Delete Portfolio/);
liveContext.fixture.portfolio.archived=1;
vm.runInContext('renderLive()',liveContext);
assert.match(liveElements.liveContent.innerHTML,/onclick="restoreLive\(\)"/);
console.log('PASS: Live view renders capital-adjusted proposal, final holdings, escaping and archived restoration.');

for(const id of ['buildMsg','jobStatus','jobStage','jobApi','jobCounters','jobElapsed']) elements[id]={textContent:'',innerHTML:''};
vm.runInContext(script.slice(script.indexOf('function fmtTime('),script.indexOf('async function resetJob(')),context);
vm.runInContext("renderJob({status:'idle'})",context);
assert.match(elements.buildMsg.innerHTML,/No active/);
vm.runInContext("renderJob({status:'running',worker_active:true,message:'Checking history',total:1148,current:0,api_requests_last_minute:0})",context);
assert.doesNotMatch(elements.buildMsg.innerHTML,/No active/);
assert.match(elements.jobApi.textContent,/API requests/);
vm.runInContext("renderJob({status:'error',stage:'Raw prices',last_error:'bad <file>',traceback:'trace <line>'})",context);
assert.equal(elements.jobApi.textContent,'');
assert.match(elements.buildMsg.innerHTML,/FAILED/);
assert.match(elements.buildMsg.innerHTML,/trace &lt;line&gt;/);
console.log('PASS: Worker progress clears idle text; inactive API counters hidden; failures expose escaped traceback.');
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/integrity.js'),'utf8'),context);
elements.integrityDashboard={innerHTML:''};elements.validateMsg={innerHTML:''};
context.integrityFixture={status:'FAIL',universe:'ETF_250M',timestamp:'2026-09-14',manifest:{dataset_id:'test-id'},
 components:{raw_price:{first_date:'20260101',last_date:'20260914',rows:3,duplicate_keys:1,conflicting_keys:1,path:'<unsafe>'}},
 checks:[{name:'raw_price_duplicate_keys',status:'FAIL',message:'Duplicate prices',details:{sample:'<script>'}}]};
vm.runInContext('renderIntegrity(integrityFixture)',context);
assert.match(elements.integrityDashboard.innerHTML,/DATASET INTEGRITY: FAIL/);
assert.match(elements.integrityDashboard.innerHTML,/test-id/);
assert.match(elements.integrityDashboard.innerHTML,/&lt;script&gt;/);
assert.match(elements.integrityDashboard.innerHTML,/View details/);
console.log('PASS: Integrity dashboard renders dataset identity, coverage, failed checks and escaped evidence.');

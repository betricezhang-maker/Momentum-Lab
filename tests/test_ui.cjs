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

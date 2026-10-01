const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('ui/index.html','utf8');
const run=html.slice(html.indexOf('let strategyGridPending='),html.indexOf('function returnStyle'));
async function scenario(fail){
 const nodes={};for(const id of ['researchUniverse','gridYears','gridFull','gridCost','gridIntegrity','gridSelection','gridPeriod','gridOut','gridSaved','gridMeta','gridBenchmark','selection'])nodes[id]={value:'',style:{},textContent:''};
 nodes.researchUniverse.value='ETF_250M';nodes.gridCost.value='5';nodes.gridBenchmark.value='none';
 const calls=[],busy=[];let release;const gate=new Promise(r=>release=r);
 const originalCheck=()=>{throw Error('Unexpected separate validation');};
 const c=vm.createContext({console,URLSearchParams,Set,document:{readyState:'loading',addEventListener(){},getElementById:id=>nodes[id],querySelectorAll:s=>[{value:s.includes('gridSel')?'Top 10':'10'}]},$:id=>nodes[id],escapeHtml:String,checkIntegrity:originalCheck,runIntegrityCheck(){},revalidateCompleteness(){},renderIntegrity(){},alert:m=>calls.push({alert:m}),calculationBusy:v=>busy.push(v),renderStrategyGrid(){},api:async(url,body)=>{calls.push({url,body});await gate;if(fail)throw Error('Dataset integrity blocked: unresolved gap');return {selection_rules:['Top 10'],periods:['Full Period'],files:{csv:'test.csv'}};}});
 vm.runInContext(fs.readFileSync('ui/integrity-workspace.js','utf8'),c);
 assert.equal(c.checkIntegrity,originalCheck,'workspace must preserve report API');
 assert.equal(calls.length,0,'script load must not launch checks');
 vm.runInContext(run,c);const pending=vm.runInContext('runStrategyGrid()',c);
 assert.deepEqual(busy,[true]);assert.match(nodes.gridIntegrity.textContent,/Run received/);
 assert.equal(calls[0].url,'/api/strategy-grid');assert.equal(calls[0].body.universe,'ETF_250M');
 await vm.runInContext('runStrategyGrid()',c);assert.equal(calls.filter(x=>x.url).length,1);
 release();await pending;assert.deepEqual(busy,[true,false]);
 assert.match(nodes.gridIntegrity.textContent,fail?/failed.*unresolved gap/:/completed/);
 assert.equal(vm.runInContext('strategyGridPending',c),false);
}
(async()=>{await scenario(false);await scenario(true);console.log('PASS: no unsolicited validation, intact report API, immediate feedback, grid dispatch, duplicate guard, visible blocking error and restored controls');})().catch(e=>{console.error(e);process.exitCode=1});

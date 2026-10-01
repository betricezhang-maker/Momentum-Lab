const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('ui/live-portfolio.js','utf8');
async function scenario(failAt){
 const nodes=Object.fromEntries(['liveMessage','paperOperationStatus','capitalAdjustment','capitalAmount','liveSelector'].map(id=>[id,{textContent:'',value:''}]));
 nodes.capitalAdjustment.value='ADD';nodes.capitalAmount.value='123';nodes.liveSelector.value='p';
 const calls=[];let release;
 const gate=new Promise(r=>release=r);
 const c=vm.createContext({console,Date,setInterval,clearInterval,$:id=>nodes[id],api:async(url,body)=>{
  calls.push({url,body});
  if(url.endsWith('paper-rebalance'))await gate;
  if(url.endsWith(failAt||'never'))throw Error('test failure');
  return url.endsWith('paper-rebalance')?{id:'journal-1'}:{portfolio:{id:'p'},rebalance_workflow:{signal_id:'next'}};
 }});
 vm.runInContext(source,c);
 vm.runInContext("liveDetail={portfolio:{id:'p'},rebalance_workflow:{signal_id:'signal-1'}};renderLive=()=>{if(paperOperation) $('paperOperationStatus').textContent=paperOperation.message;};",c);
 const pending=vm.runInContext('applyPaperRebalance()',c);
 assert.match(nodes.paperOperationStatus.textContent,/Applying/);
 await vm.runInContext('applyPaperRebalance()',c);
 assert.equal(calls.length,1,'duplicate click must not submit');
 assert.equal(calls[0].body.capital_amount,123);
 assert.equal(calls[0].body.signal_id,'signal-1');
 release();await pending;
 assert.equal(vm.runInContext('paperApplyPending',c),false);
 assert.equal(calls.some(x=>x.url.endsWith('rebalance-preview')),false);
 assert.match(nodes.paperOperationStatus.textContent,failAt==='paper-rebalance'?/could not be confirmed/:failAt==='detail'?/was saved.*refresh failed/:/completed and portfolio refreshed/);
}
(async()=>{await scenario();await scenario('paper-rebalance');await scenario('detail');console.log('PASS: immediate feedback, duplicate-click prevention, submitted capital, persistent success, API failure and saved-but-refresh-failed handling');})().catch(e=>{console.error(e);process.exitCode=1});

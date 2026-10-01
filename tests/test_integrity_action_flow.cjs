const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const elements={integrityDashboard:{innerHTML:''},researchUniverse:{value:'ETF_250M'}};let request=null,rendered=null;
const context=vm.createContext({$:id=>elements[id],escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;'),prompt:(label,initial)=>label.includes('Explanation')?'accepted gap':'',
 fetch:async(url,options)=>{request={url,options};return {ok:true,json:async()=>({ok:true,report:{universe:'ETF_250M',research_allowed:true,completeness:{findings:[{issue_id:'CMP-1',resolution_status:'CONFIRMED',blocks_research:false}],confirmed_count:2,unresolved_blocking_count:0},structural_status:'PASS'}})}},
 setTimeout:fn=>fn()});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/integrity.js'),'utf8'),context);
context.latestIntegrityReport={universe:'ETF_250M'};
vm.runInContext('acknowledgeCompleteness("CMP-1")',context);
setTimeout(()=>{assert.equal(request.url,'/api/completeness/acknowledge');const body=JSON.parse(request.options.body);assert.equal(body.issue_id,'CMP-1');assert.equal(body.explanation,'accepted gap');console.log('PASS: UI acknowledgment submits, verifies fresh CONFIRMED status, and persists the selected issue ID.');},0);

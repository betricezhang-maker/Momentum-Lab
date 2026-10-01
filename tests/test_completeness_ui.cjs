const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements={integrityDashboard:{innerHTML:''}}, downloaded=[];
const context=vm.createContext({$:id=>elements[id],escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;'),
 Blob, URL:{createObjectURL:b=>{downloaded.push(b);return 'blob:test'},revokeObjectURL:()=>{}},
 document:{createElement:()=>({click(){}})},setTimeout:fn=>fn()});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../ui/integrity.js'),'utf8'),context);
context.report={status:'FAIL',structural_status:'PASS',manifest:{},completeness:{status:'FAIL',scope:{lookback_sessions:10},counts:{UNEXPLAINED_MISSING:3},
 examples:[{ticker:'<A>',date:'20260915',end_date:'20260915',affected_component:'raw_price',classification:'UNEXPLAINED_MISSING',severity:'FAIL',evidence_source:'calendar',signal_impact:'Excluded'}],findings:Array(50).fill({ticker:'A'}),limitations:[]}};
vm.runInContext('renderIntegrity(report);exportCompleteness()',context);
assert.match(elements.integrityDashboard.innerHTML,/Structural integrity: PASS/);
assert.match(elements.integrityDashboard.innerHTML,/Data completeness: FAIL/);
context.report.completeness.findings[0]={...context.report.completeness.examples[0],resolution_status:'CONFIRMED',overridable:true,explanation:'Accepted <gap>',inherited_from:'parent-confirmation',reopen_issue_id:'CMP-parent'};
context.report.research_allowed=true;
vm.runInContext('renderIntegrity(report)',context);
assert.match(elements.integrityDashboard.innerHTML,/&lt;A>/);
assert.match(elements.integrityDashboard.innerHTML,/CONFIRMED/);
assert.match(elements.integrityDashboard.innerHTML,/Accepted &lt;gap>/);
assert.match(elements.integrityDashboard.innerHTML,/Inherited from raw-price confirmation/);
assert.match(elements.integrityDashboard.innerHTML,/Reopen/);
assert.match(elements.integrityDashboard.innerHTML,/Research allowed: YES/);
assert.match(elements.integrityDashboard.innerHTML,/not unique ticker-days/);
downloaded[0].text().then(text=>{assert.equal(JSON.parse(text).completeness.findings.length,50);console.log('PASS: completeness statuses, escaping and full evidence export.');});

const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const elements={researchAuditResult:{innerHTML:''}};
const context=vm.createContext({$:id=>elements[id],escapeHtml:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/pagination.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../ui/research-audit.js'),'utf8'),context);
context.fixture={manifest:{audit_id:'fixture',formula:'This is not Sharpe Ratio.'},integrity:{status:'WARNING'},
 trace:{summary:{mom_score:2.391930123456789},price_inputs:[{observation:0,trade_date:'2026-09-04',adjusted_close:1.23456789012345}],daily_returns:[]},
 ranking:[{ticker:'<unsafe>',rank:1}],top10_target:[],comparisons:[{source:'Strategy Grid',status:'UNAVAILABLE',reason:'No saved target'}],
 universe_snapshot:[],excluded_securities:[],mom_calculation:[],export_path:'results/audit fixture.zip'};
vm.runInContext('renderResearchAudit(fixture)',context);
assert.match(elements.researchAuditResult.innerHTML,/This is not Sharpe Ratio/);
assert.match(elements.researchAuditResult.innerHTML,/WARNING/);
assert.match(elements.researchAuditResult.innerHTML,/&lt;unsafe>/);
assert.match(elements.researchAuditResult.innerHTML,/1.23456789012345/);
assert.match(elements.researchAuditResult.innerHTML,/UNAVAILABLE/);
assert.match(elements.researchAuditResult.innerHTML,/audit%20fixture.zip/);
vm.runInContext("renderResearchAudit({blocked:true,integrity:{status:'FAIL'}})",context);
assert.match(elements.researchAuditResult.innerHTML,/FAIL/);
assert.doesNotMatch(elements.researchAuditResult.innerHTML,/Export Research Audit Package/);
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
assert.match(html,/A. Portfolio Audit Trail/);assert.match(html,/B. Research \/ MOM Calculation Audit/);
assert.match(html,/id="liveAudit"/);assert.match(html,/Export portfolio CSVs and audit/);
console.log('PASS: Research audit precision, escaping, blocked integrity, export and preserved portfolio audit.');

/* Shared Data Manager / pre-research integrity view. Validation never downloads. */
let latestIntegrityReport=null, integrityPage=1, integrityPageSize=50;
function exportCompleteness(){
 if(!latestIntegrityReport)return;
 const blob=new Blob([JSON.stringify(latestIntegrityReport,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='momentum-completeness-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function completenessUniverse(){return latestIntegrityReport?.universe||$('researchUniverse').value;}
async function revalidateCompleteness(){ await checkIntegrity(completenessUniverse(),'full'); }
async function acknowledgeCompleteness(issueId){
 const category='MANUALLY_ACCEPTED_GAP';
 const explanation=prompt('Explanation for manual acceptance (does not verify suspension or repair missing data):',''); if(!explanation?.trim())return;
 const reference=prompt('Optional supporting reference:','')||'';
 try{
  const r=await fetch('/api/completeness/acknowledge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe:completenessUniverse(),issue_id:issueId,category,explanation,reference})});
  const result=await r.json(); if(!r.ok||!result.ok)throw new Error(result.error||'Acknowledgment failed');
  const confirmed=(result.report.completeness?.findings||[]).find(x=>x.issue_id===issueId);
  if(!confirmed||confirmed.resolution_status!=='CONFIRMED')throw new Error('The provider saved the request but fresh validation did not confirm this issue. Click Revalidate and inspect the server response.');
  renderIntegrity(result.report);
 }catch(e){showIntegrityActionError(e);}
}
function showIntegrityActionError(error){const target=$('integrityDashboard');if(target)target.insertAdjacentHTML('afterbegin','<p class="bad"><b>Integrity action failed:</b> '+escapeHtml(error.message||String(error))+'</p>');}
async function revokeCompleteness(issueId){
 try{const r=await fetch('/api/completeness/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe:completenessUniverse(),issue_id:issueId})});const result=await r.json();if(!r.ok||!result.ok)throw new Error(result.error||'Reopen failed');renderIntegrity(result.report);}catch(e){showIntegrityActionError(e);}
}
async function repairCompleteness(issueId){
 const r=await fetch('/api/completeness/repair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe:$('researchUniverse').value,issue_ids:[issueId]})});
 const result=await r.json(); if(!result.ok)throw new Error(result.error||'Repair failed'); renderIntegrity(result.integrity);
}
function integritySummary(report){
 const m=report.manifest||{};
 return '<b>DATASET INTEGRITY: '+escapeHtml(report.status)+(report.cached?' — cached':'')+'</b><br>'+escapeHtml(report.universe)+
  ' · Dataset ID: '+escapeHtml(m.dataset_id||'unavailable')+'<br>Adjusted prices through '+escapeHtml(m.adjusted_price_last_date||'—')+
  ' · Calendar through '+escapeHtml(m.calendar_last_date||'—')+' · Eligible securities: '+escapeHtml(m.eligible_tickers||0);
}
function renderIntegrity(report){
 latestIntegrityReport=report;
 const components=report.components||{};
 let html=integritySummary(report)+'<p>Validated '+escapeHtml(report.timestamp)+'</p>';
 if(report.completeness){
  const c=report.completeness;
  html+='<h3>Structural integrity: '+escapeHtml(report.structural_status)+' · Data completeness: '+escapeHtml(c.status)+'</h3>'+
   '<p>Completeness checked only between each ETF’s first and last stored price dates; leading/trailing omissions are not assessed.</p><p>Coverage scope: '+escapeHtml(JSON.stringify(c.scope))+'</p><p>Missing component-sessions / evidence findings by classification (not unique ticker-days): '+escapeHtml(JSON.stringify(c.counts))+'</p>'+
   '<button class="secondary" onclick="revalidateCompleteness()">Revalidate</button> <button class="secondary" onclick="exportCompleteness()">Export full completeness evidence (JSON)</button> <button class="secondary" onclick="removeIdenticalDuplicates()">Remove identical duplicates</button>'+
   '<p>Latest-price freshness: raw '+escapeHtml(report.manifest?.raw_price_last_date||'—')+' · adjusted '+escapeHtml(report.manifest?.adjusted_price_last_date||'—')+'</p>'+
   '<p>Research allowed: '+(report.research_allowed?'YES':'NO')+' · Unresolved blocking findings: '+escapeHtml(c.unresolved_blocking_count)+' · Confirmed findings: '+escapeHtml(c.confirmed_count||0)+'</p>'+
   (report.structural_status==='FAIL'?'<p>Structural failures remain blocking and cannot be confirmed away. Correct the failed structural checks below.</p>':'')+
   '<table><caption>CONFIRMED means manually reviewed and accepted with the recorded explanation. It does not mean repaired data or independently verified suspension. Incomplete MOM windows remain excluded.</caption><tr><th>Issue ID</th><th>Ticker / dates</th><th>Component</th><th>Original reason / severity</th><th>Blocks research</th><th>Status / explanation / evidence</th><th>Action</th></tr>';
  for(const r of c.findings||c.examples||[]){const action=r.resolution_status==='CONFIRMED'?'<button class="secondary" onclick="revokeCompleteness(\''+escapeHtml(r.reopen_issue_id||r.issue_id)+'\')">Reopen</button>':(r.overridable?'<button class="secondary" onclick="repairCompleteness(\''+escapeHtml(r.issue_id)+'\')">Repair Missing Data</button> <button class="secondary" onclick="acknowledgeCompleteness(\''+escapeHtml(r.issue_id)+'\')">Acknowledge with reason</button>':'');const detail=[r.resolution_status,r.explanation||'',r.confirmed_at||'',r.inherited_from?'Inherited from raw-price confirmation '+r.inherited_from:'',r.confirmation?.reference||'',r.evidence_source||''].filter(Boolean).join(' · ');html+='<tr data-issue-id="'+escapeHtml(r.issue_id)+'">'+[r.issue_id,r.ticker+' '+r.date+' → '+r.end_date,r.affected_component,r.classification+' / '+r.severity,r.blocks_research?'YES':'NO',detail,action].map((v,i)=>i===6?'<td>'+v+'</td>':'<td>'+escapeHtml(v)+'</td>').join('')+'</tr>';}
  for(const h of c.confirmation_history||[])if(h.resolution_status==='RESOLVED')html+='<tr><td>'+escapeHtml(h.issue_id)+'</td><td>'+escapeHtml(h.ticker+' '+h.date+' → '+h.end_date)+'</td><td>'+escapeHtml(h.affected_component)+'</td><td>'+escapeHtml(h.original_finding?.classification)+'</td><td>NO</td><td>RESOLVED · '+escapeHtml(h.explanation)+'</td><td>History retained</td></tr>';
  const totalFindings=(c.findings||c.examples||[]).length,totalPages=Math.max(1,Math.ceil(totalFindings/integrityPageSize)); integrityPage=Math.min(integrityPage,totalPages);
  html+='</table><p>Showing '+(totalFindings?1:0)+'–'+Math.min(integrityPage*integrityPageSize,totalFindings)+' of '+totalFindings+' findings · '+(report.review_groups||[]).length+' review groups</p><p><button class="secondary" onclick="integrityPage=Math.max(1,integrityPage-1);renderIntegrity(latestIntegrityReport)">Previous</button> Page '+integrityPage+' / '+totalPages+' <button class="secondary" onclick="integrityPage=Math.min('+totalPages+',integrityPage+1);renderIntegrity(latestIntegrityReport)">Next</button></p><p>'+escapeHtml((c.limitations||[]).join(' '))+'</p>';
  if(c.confirmation_history?.length)html+='<details><summary>Confirmation history (including reopened and resolved dates)</summary><pre>'+escapeHtml(JSON.stringify(c.confirmation_history,null,2))+'</pre></details>';
 }
 html+='<table><caption>Dataset component coverage and canonical-key integrity</caption><thead><tr><th>Component</th><th>First → last date</th><th>Rows / unique securities</th><th>Duplicate keys / conflicts</th><th>Status and details</th></tr></thead><tbody>';
 for(const [name,c] of Object.entries(components)){
  const checks=(report.checks||[]).filter(x=>x.name.startsWith(name));
  const status=checks.some(x=>x.status==='FAIL')?'FAIL':checks.some(x=>x.status==='WARNING')?'WARNING':'PASS';
  html+='<tr><td>'+escapeHtml(name)+'</td><td>'+escapeHtml(c.first_date)+' → '+escapeHtml(c.last_date)+'</td><td>'+escapeHtml(c.rows)+' / '+escapeHtml(c.unique_tickers||0)+'</td><td>'+escapeHtml(c.duplicate_keys)+' / '+escapeHtml(c.conflicting_keys)+'</td><td>'+status+'<details><summary>View details</summary><pre>'+escapeHtml(JSON.stringify(c,null,2))+'</pre></details></td></tr>';
 }
 html+='</tbody></table><details open><summary>All checks, including ETF classification and cross-file coverage</summary><ul>';
 for(const c of report.checks||[])html+='<li><b>'+escapeHtml(c.status)+' · '+escapeHtml(c.name)+'</b>: '+escapeHtml(c.message)+'<details><summary>Evidence</summary><pre>'+escapeHtml(JSON.stringify(c.details,null,2))+'</pre></details></li>';
 html+='</ul></details>';
 for(const id of ['integrityDashboard','validateMsg'])if($(id))$(id).innerHTML=html;
 if(typeof attachBulkReviewControls==='function')attachBulkReviewControls(report);
 applyIntegrityPagination();
}
function applyIntegrityPagination(){
 const host=$('integrityDashboard');if(!host)return;const rows=[...host.querySelectorAll('tr[data-issue-id]')];if(!rows.length)return;
 const pages=Math.max(1,Math.ceil(rows.length/integrityPageSize));integrityPage=Math.min(integrityPage,pages);const first=(integrityPage-1)*integrityPageSize;
 rows.forEach((r,i)=>r.hidden=i<first||i>=first+integrityPageSize);
 let bar=$('integrityPager');if(!bar){bar=document.createElement('div');bar.id='integrityPager';host.appendChild(bar);}
 bar.innerHTML='Showing '+(rows.length?first+1:0)+'–'+Math.min(first+integrityPageSize,rows.length)+' of '+rows.length+' findings · <label>Page size <select id="integrityPageSize"><option>25</option><option selected>50</option><option>100</option><option>250</option></select></label> <button>First</button> <button>Previous</button> Page '+integrityPage+' / '+pages+' <button>Next</button> <button>Last</button>';
 bar.querySelector('#integrityPageSize').value=String(integrityPageSize);bar.querySelectorAll('button')[0].onclick=()=>{integrityPage=1;applyIntegrityPagination()};bar.querySelectorAll('button')[1].onclick=()=>{integrityPage=Math.max(1,integrityPage-1);applyIntegrityPagination()};bar.querySelectorAll('button')[2].onclick=()=>{integrityPage=Math.min(pages,integrityPage+1);applyIntegrityPagination()};bar.querySelectorAll('button')[3].onclick=()=>{integrityPage=pages;applyIntegrityPagination()};bar.querySelector('#integrityPageSize').onchange=e=>{integrityPageSize=Number(e.target.value);integrityPage=1;applyIntegrityPagination()};
}
async function removeIdenticalDuplicates(){
 try{const r=await fetch('/api/repair-safe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe:completenessUniverse()})});const x=await r.json();if(!r.ok||!x.ok)throw new Error(x.error||'Duplicate repair failed');renderIntegrity(x.report);}
 catch(e){showIntegrityActionError(e);}
}
async function checkIntegrity(universe,mode='full'){
 const response=await fetch('/api/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe,mode})});
 const report=await response.json();
 if(!report.checks)throw new Error(report.error||'Integrity check unavailable');
 renderIntegrity(report);
 return report;
}
async function runIntegrityCheck(){
 $('integrityDashboard').textContent='Checking local files; no downloads…';
 try{await checkIntegrity($('researchUniverse').value)}catch(e){$('integrityDashboard').textContent=e.message}
}

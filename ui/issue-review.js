/* Bulk review never applies uploaded decisions until the explicit Apply click. */
let reviewPreview=null,bulkTimer=null,bulkBusy=false,bulkUniverse=null;
const reviewFilters={status:'',ticker:'',component:'',classification:'',blocking:false};
async function reviewPost(path,body){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Integrity operation failed.');return result;
}
function reviewMessage(text){const el=document.getElementById('reviewMessage');if(el)el.textContent=text;}
function attachBulkReviewControls(report){
 const host=document.getElementById('integrityDashboard');if(!host)return;
 const groups=report.review_groups||[];
 host.insertAdjacentHTML('afterbegin','<section id="bulkReview"><h3>Bulk issue review</h3><p>'+escapeHtml(groups.length)+' unique review groups · '+escapeHtml(groups.reduce((n,g)=>n+g.issue_ids.length,0))+' underlying findings (including structural warnings/errors).</p>'+
 '<button onclick="exportIssueReview()">Export issues for review (Excel)</button> <label>Import reviewed issues <input type="file" accept=".xlsx" onchange="importIssueReview(this)"></label> '+
 '<button onclick="repairReviewedSelection(false)">Repair selected</button> <button onclick="repairReviewedSelection(true)">Repair all eligible missing-data issues</button>'+
 '<p><label>Status <select id="reviewStatus" onchange="reviewFilters.status=this.value;filterReviewRows()"><option value="">All</option><option>UNRESOLVED</option><option>CONFIRMED</option><option>REVIEW_REQUIRED</option></select></label> '+
 '<label><input id="reviewBlocking" type="checkbox" onchange="reviewFilters.blocking=this.checked;filterReviewRows()">Blocking only</label> '+
 '<label>Ticker <input id="reviewTicker" oninput="reviewFilters.ticker=this.value;filterReviewRows()"></label> '+
 '<label>Component <input id="reviewComponent" oninput="reviewFilters.component=this.value;filterReviewRows()" placeholder="raw_price"></label> '+
 '<label>Classification <input id="reviewClass" oninput="reviewFilters.classification=this.value;filterReviewRows()" placeholder="UNEXPLAINED_MISSING"></label></p>'+
 '<button onclick="selectVisibleReviewRows()">Select visible findings</button> <span id="reviewVisibleCount"></span><p id="reviewMessage" role="status"></p><div id="reviewPreview"></div></section>');
 for(const row of host.querySelectorAll('tr[data-issue-id]')){
  const box=document.createElement('input');box.type='checkbox';box.className='reviewSelection';box.value=row.dataset.issueId;box.setAttribute('aria-label','Select '+row.dataset.issueId);row.cells[0].prepend(box);
 }
 for(const [id,value] of [['reviewStatus',reviewFilters.status],['reviewTicker',reviewFilters.ticker],['reviewComponent',reviewFilters.component],['reviewClass',reviewFilters.classification]])document.getElementById(id).value=value;
 document.getElementById('reviewBlocking').checked=reviewFilters.blocking;filterReviewRows();
 if(reviewPreview)showReviewPreview(reviewPreview);
}
function filterReviewRows(){
 const host=document.getElementById('integrityDashboard');if(!host)return;
 const findings=new Map((latestIntegrityReport?.completeness?.findings||[]).map(r=>[r.issue_id,r]));let visible=0;
 for(const row of host.querySelectorAll('tr[data-issue-id]')){
  const r=findings.get(row.dataset.issueId)||{};
  const match=(!reviewFilters.status||r.resolution_status===reviewFilters.status)&&(!reviewFilters.blocking||r.blocks_research)&&
    String(r.ticker||'').toLowerCase().includes(reviewFilters.ticker.toLowerCase())&&String(r.affected_component||'').toLowerCase().includes(reviewFilters.component.toLowerCase())&&String(r.classification||'').toLowerCase().includes(reviewFilters.classification.toLowerCase());
  row.hidden=!match;if(match)visible++;
 }
 document.getElementById('reviewVisibleCount').textContent=visible+' completeness findings shown. Export includes all findings regardless of filters.';
}
function selectVisibleReviewRows(){for(const row of document.querySelectorAll('#integrityDashboard tr[data-issue-id]'))if(!row.hidden)row.querySelector('.reviewSelection').checked=true;}
async function exportIssueReview(){
 try{reviewMessage('EXPORT · validating dataset and collecting all findings…');const r=await reviewPost('/api/completeness/export-review',{universe:completenessUniverse()});
 const bytes=Uint8Array.from(atob(r.workbook),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
 const a=document.createElement('a');a.href=url;a.download=r.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);reviewMessage('EXPORT · completed · '+r.groups+' review groups / '+r.findings+' findings written. Edit only the three yellow columns.');
 }catch(e){reviewMessage(e.message);}
}
async function importIssueReview(input){
 const file=input.files?.[0];if(!file)return;reviewPreview=null;
 try{if(file.size>12000000)throw new Error('Workbook exceeds 12 MB.');reviewMessage('UPLOAD · reading '+escapeHtml(file.name)+' · '+file.size.toLocaleString()+' bytes…');
 const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
 reviewMessage('PARSED · workbook bytes read · validating review rows…');const r=await reviewPost('/api/completeness/import-review',{universe:completenessUniverse(),workbook:btoa(text)});r.universe=completenessUniverse();reviewPreview=r;showReviewPreview(r);const total=(r.decisions||[]).length,valid=(r.decisions||[]).filter(d=>d.status==='VALID').length,rejected=(r.decisions||[]).filter(d=>d.status==='REJECTED').length;reviewMessage('PREVIEW · '+total+' rows processed · '+valid+' valid · '+rejected+' rejected · no production data or review status changed.');
 }catch(e){reviewMessage(e.message);}finally{input.value='';}
}
function showReviewPreview(result){
 const el=document.getElementById('reviewPreview');if(!el)return;
 el.innerHTML='<h4>Reviewed decisions preview</h4><p>File uploaded and validated. No production data or review status has changed.</p><p>'+escapeHtml(JSON.stringify(result.counts||{}))+' · Inherited adjusted-price confirmations: '+escapeHtml(result.inherited_count||0)+'</p>'+
 '<button id="applyReviewButton" onclick="applyIssueReview()" '+(bulkBusy?'disabled':'')+'>Apply reviewed decisions</button><table><tr><th>Review group / issue IDs</th><th>Requested action</th><th>Reason</th><th>Preview status</th><th>Rejected/stale reason or inherited dates</th></tr>'+
 (result.decisions||[]).map(d=>'<tr>'+[d.group_id+' '+(d.issue_ids||[]).join(', '),d.action,d.reason,d.status,d.error||JSON.stringify(d.inherited||[])].map(v=>'<td>'+escapeHtml(v)+'</td>').join('')+'</tr>').join('')+'</table>';
}
async function applyIssueReview(){
 if(!reviewPreview||bulkBusy)return;
 try{reviewMessage('APPLY · creating operation and starting…');bulkUniverse=reviewPreview.universe;const result=await reviewPost('/api/completeness/apply-review',{universe:bulkUniverse,preview_id:reviewPreview.preview_id});watchBulkJob(result.job);}
 catch(e){reviewMessage(e.message);}
}
async function repairReviewedSelection(all){
 if(bulkBusy)return;
 const ids=[...document.querySelectorAll('#integrityDashboard .reviewSelection:checked')].map(x=>x.value);
 if(!all&&!ids.length){reviewMessage('Select at least one finding to repair.');return;}
 try{bulkUniverse=completenessUniverse();const r=await reviewPost('/api/completeness/repair-batch',{universe:bulkUniverse,issue_ids:ids,all_eligible:all});watchBulkJob(r.job);}catch(e){reviewMessage(e.message);}
}
function watchBulkJob(job){
 bulkBusy=true;if(bulkTimer)clearTimeout(bulkTimer);reviewMessage('Operation '+escapeHtml(job.id||'')+' queued. Production data changes only after staged validation succeeds.');
 const poll=async()=>{try{
  const r=await fetch('/api/completeness/bulk-status',{cache:'no-store'});const {job}=await r.json();
  reviewMessage('Operation '+escapeHtml(job.id||'')+' · '+(job.stage||job.status)+' · '+(job.current||0)+' / '+(job.total||0)+' · '+(job.message||''));
  if(job.status==='running'){bulkTimer=setTimeout(poll,1000);return;}
  bulkBusy=false;if(job.status==='failed')throw new Error(job.error||'Bulk operation failed.');
  const result=job.result||{};reviewPreview=null;
  if(result.report||result.integrity)renderIntegrity(result.report||result.integrity);
  const target=document.getElementById('reviewPreview');if(target)target.innerHTML='<h4>Bulk operation results</h4><pre>'+escapeHtml(JSON.stringify({committed:result.committed,decisions:result.decisions,repair:result.repair?{committed:result.repair.committed,results:result.repair.results,error:result.repair.error}:undefined,results:result.results,error:result.error},null,2))+'</pre>';
  reviewMessage('Operation '+escapeHtml(job.id||'')+' finished. Review per-issue results and rejected rows below. Empty provider responses are not counted as repairs.');
 }catch(e){bulkBusy=false;reviewMessage(e.message+' Revalidate before retrying.');}};bulkTimer=setTimeout(poll,300);
}

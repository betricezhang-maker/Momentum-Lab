/* One Data Manager integrity workspace. Findings and previews are paged separately. */
const ws={page:1,size:50,previewPage:1,previewSize:50,sort:'ticker',filters:{},report:null,
  result:null,preview:null,selected:new Set(),busy:false,job:null,timer:null,started:0};
const wsEsc=v=>escapeHtml(String(v??''));
const wsUniverse=()=>document.getElementById('dataUniverse')?.value||document.getElementById('researchUniverse')?.value||'ETF_250M';
const wsFind=(id)=>document.getElementById(id);
const wsBuild=()=>wsFind('wsBuild')?.value||'';
let wsBuildRequest=0;
async function wsLoadBuilds(){
 const request=++wsBuildRequest,universe=wsUniverse(),selected=wsBuild(),r=await fetch('/api/pending-builds?'+new URLSearchParams({universe}),{cache:'no-store'}),data=await r.json();
 if(request!==wsBuildRequest||universe!==wsUniverse())return;
 if(!data.ok)throw new Error(data.error);
 wsFind('wsBuild').innerHTML='<option value="">Production dataset</option>'+(data.builds||[]).filter(b=>b.status!=='PUBLISHED').map(b=>'<option value="'+wsEsc(b.id)+'" '+(!b.download_complete?'disabled':'')+'>'+wsEsc(b.created_at+' · '+b.status+' · '+b.id.slice(0,8))+'</option>').join('');
 if([...wsFind('wsBuild').options].some(o=>o.value===selected))wsFind('wsBuild').value=selected;
 wsBuildLabel();
 wsFind('wsBuildCount').textContent=(data.builds||[]).filter(b=>b.status!=='PUBLISHED').length+' retained build(s) for '+universe;
}
function wsBuildLabel(){wsFind('wsTargetNotice').textContent=wsBuild()?'RETAINED BUILD — review and repairs affect staged files only. Production remains unchanged until Publish.':'PRODUCTION — review and repair affect the current published dataset.';wsFind('wsPublish').disabled=ws.busy||!wsBuild()}
async function wsRefreshBuilds(){
 const button=wsFind('wsBuildRefresh');if(ws.busy||button.disabled)return;
 button.disabled=true;button.textContent='Refreshing retained builds…';
 wsFind('wsBuildCount').textContent='Loading retained builds…';
 try{await wsLoadBuilds();wsFind('wsBuildCount').textContent+=' · Refreshed '+new Date().toLocaleTimeString()+'. Choose a retained build in Review dataset.'}
 catch(e){wsFind('wsBuildCount').textContent='Could not refresh retained builds: '+e.message}
 finally{button.disabled=ws.busy;button.textContent='Refresh retained builds'}
}
async function wsChangeBuild(){ws.page=1;ws.selected.clear();ws.preview=null;wsFind('wsPreview').innerHTML='';sessionStorage.removeItem('momentumIntegrityPreview');wsBuildLabel();await wsCheck()}
function wsPublish(){
 if(ws.busy){wsFind('wsTargetNotice').textContent='Publication has not started: wait for the current integrity operation to finish.';return}
 if(!wsBuild()){wsFind('wsTargetNotice').textContent='Publication has not started: select an AWAITING_REVIEW retained build in Review dataset first.';return}
 const id=wsBuild(),universe=wsUniverse(),host=wsFind('wsPublishReview');
 wsFind('wsPublish').insertAdjacentElement('afterend',host);
 host.style.cssText='display:block;border:2px solid #2563eb;padding:14px;margin:12px 0;background:#eff6ff;color:#172554;white-space:normal';
 host.innerHTML='<p>Publish retained build <b>'+wsEsc(id)+' ('+wsEsc(universe)+')</b>? This revalidates the reviewed files and replaces production only if validation allows it, with a rollback backup. Refresh retained builds only reloads the list.</p><button id="wsPublishConfirm">Confirm publication</button> <button id="wsPublishCancel">Cancel</button>';
 wsFind('wsTargetNotice').textContent='Click received — publication has NOT started. Review the selected build below, then choose Confirm publication.';
 wsFind('wsPublishCancel').onclick=()=>{host.innerHTML='';host.style.display='none';wsBuildLabel()};
 wsFind('wsPublishConfirm').onclick=()=>wsExecutePublish(universe,id);
 host.scrollIntoView({block:'center'});
 wsFind('wsPublishConfirm').focus({preventScroll:true});
}
async function wsExecutePublish(universe,id){if(ws.busy)return;
 if(universe!==wsUniverse()||id!==wsBuild()){wsFind('wsPublishReview').textContent='The selected dataset changed. Click Revalidate & publish again to review the correct build.';return}
 wsFind('wsPublishReview').innerHTML='';
 ws.started=Date.now();wsBusy(true);wsMessage('Publish retained build','QUEUED','Revalidating before publication.');
 wsFind('wsOperation').scrollIntoView({block:'center'});
 try{const r=await wsPost('/api/pending-builds/publish',{universe:wsUniverse(),confirmation:'PUBLISH'});wsWatch(r.job)}catch(e){wsBusy(false);wsMessage('Publish retained build','FAILED',e.message)}
}
function wsInit(){
 const manager=wsFind('manager'),host=wsFind('integrityDashboard');if(!manager||!host)return;
 const card=host.closest('.card');card.classList.add('wide');card.style.width='100%';
 const dataset=wsFind('dataUniverse')?.closest('.card');if(dataset)dataset.insertAdjacentElement('afterend',card);
 const previous=card.querySelector('button[onclick="runIntegrityCheck()"]');if(previous)previous.remove();
 host.style.overflow='visible';host.innerHTML='<div><label>Review dataset <select id="wsBuild"><option value="">Production dataset</option></select></label> <button id="wsBuildRefresh">Refresh retained builds</button> <span id="wsBuildCount" role="status"></span> <button id="wsPublish" disabled>Revalidate &amp; publish retained build</button><p id="wsTargetNotice"></p></div><div id="wsSummary">Run an integrity check to inspect this dataset.</div>'+
 '<div id="wsToolbar"><button id="wsCheck">Run integrity check</button> <button id="wsExportPage">Export current page</button> <button id="wsExportFiltered">Export all filtered results</button> <button id="wsExportAll">Export all findings</button> <button id="wsRepair">Repair selected</button> <span id="wsSelectedCount" role="status">0 findings selected</span> <button id="wsRepairAll">Repair all eligible</button> <button id="wsDuplicates">Remove identical duplicates</button></div>'+
 '<div id="wsPublishReview" role="region" aria-label="Publication confirmation"></div><div id="wsOperation" role="status" aria-live="polite">No integrity operation running.</div>'+
 '<section id="wsUpload"><h4>Bulk review</h4><label>Choose reviewed workbook <input id="wsFile" type="file" accept=".xlsx"></label><button id="wsUploadButton">Upload and validate</button><div id="wsPreview"></div></section>'+
 '<section id="wsFindings"><h4>Findings</h4><div id="wsFilters"></div><div id="wsTable"></div><div id="wsPager"></div></section>'+
 '<details><summary>Coverage, fingerprint, and technical evidence</summary><pre id="wsEvidence"></pre></details>';
 wsFind('wsCheck').onclick=()=>wsCheck();wsFind('wsExportPage').onclick=()=>wsExport('page');
 wsFind('wsExportFiltered').onclick=()=>wsExport('filtered');wsFind('wsExportAll').onclick=()=>wsExport('all');
 wsFind('wsRepair').onclick=()=>wsRepair(false);wsFind('wsRepairAll').onclick=()=>wsRepair(true);
 wsFind('wsDuplicates').onclick=()=>wsDuplicate();wsFind('wsUploadButton').onclick=()=>wsUpload();
 wsFind('wsBuild').onchange=wsChangeBuild;wsFind('wsPublish').onclick=wsPublish;wsFind('wsBuildRefresh').onclick=wsRefreshBuilds;
 wsLoadBuilds().catch(e=>wsMessage('Retained builds','FAILED',e.message));
 wsFind('dataUniverse')?.addEventListener('change',()=>{wsFind('wsBuild').value='';wsLoadBuilds().catch(e=>wsMessage('Retained builds','FAILED',e.message));ws.page=1;ws.selected.clear();ws.preview=null;wsFind('wsPreview').innerHTML='';wsLoadPage()});
 wsFind('wsFilters').innerHTML='<label>Status <select data-key="status"><option value="">All</option><option>UNRESOLVED</option><option>CONFIRMED</option><option>REVIEW_REQUIRED</option></select></label> '+
 '<label>Ticker <input data-key="ticker"></label> <label>Component <input data-key="component"></label> '+
 '<label>Classification <input data-key="classification"></label> <label>Review group <input data-key="group"></label> '+
 '<label><input type="checkbox" data-key="blocking"> Blocking only</label> <label><input type="checkbox" data-key="repair_eligible"> Repair eligible</label> '+
 '<label>Sort <select id="wsSort"><option>ticker</option><option>date</option><option>severity</option><option>status</option><option>component</option><option>updated</option></select></label>';
 for(const el of wsFind('wsFilters').querySelectorAll('[data-key]')){
  el.addEventListener(el.type==='text'?'input':'change',()=>{ws.filters[el.dataset.key]=el.type==='checkbox'?el.checked:el.value;ws.page=1;wsLoadPage()});
 }
 wsFind('wsSort').onchange=e=>{ws.sort=e.target.value;ws.page=1;wsLoadPage()};
 document.querySelectorAll('#manager button[onclick="validateFiles()"],#manager button[onclick="runIntegrityCheck()"]:not(#wsCheck)').forEach(b=>b.onclick=()=>wsCheck());
 wsResume();
}
function wsMessage(name,stage,message,extra={}){
 const elapsed=ws.started?Math.round((Date.now()-ws.started)/1000):0;
 wsFind('wsOperation').innerHTML='<strong>'+wsEsc(name)+' · '+wsEsc(stage)+'</strong> · '+wsEsc(message)+
 (extra.current!=null&&extra.total!=null?' · '+wsEsc(extra.current)+' / '+wsEsc(extra.total):stage==='COMPLETED'||stage==='FAILED'||stage==='DOWNLOAD_READY'?'':' · activity in progress')+
 ' · elapsed '+elapsed+'s'+(extra.id?' · operation '+wsEsc(extra.id):'')+
 (extra.updated_at?' · last update '+wsEsc(extra.updated_at):'');
}
function wsSelectionStatus(){const count=ws.selected.size;wsFind('wsSelectedCount').textContent=count+' finding'+(count===1?'':'s')+' selected';wsFind('wsRepair').disabled=ws.busy||count===0;}
function wsBusy(value){ws.busy=value;wsFind('wsOperation').classList.toggle('working',value);for(const button of wsFind('wsToolbar').querySelectorAll('button'))button.disabled=value;
 for(const id of ['dataUniverse','wsBuild','wsBuildRefresh'])if(wsFind(id))wsFind(id).disabled=value;
 wsBuildLabel();
 wsSelectionStatus();
 wsFind('wsUploadButton').disabled=value;const apply=wsFind('wsApply');if(apply)apply.disabled=value||!ws.preview?.valid_count;}
async function wsPost(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,build_id:wsBuild()})});
 const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Operation failed.');return result;}
async function wsCheck(){if(ws.busy)return;ws.started=Date.now();wsBusy(true);wsMessage('Integrity check','QUEUED','Click received; starting validation.');
 try{const r=await wsPost('/api/completeness/check-job',{universe:wsUniverse()});wsWatch(r.job);}catch(e){wsBusy(false);wsMessage('Integrity check','FAILED',e.message)}}
async function wsLoadPage(){
 const q=new URLSearchParams({universe:wsUniverse(),build_id:wsBuild(),page:ws.page,size:ws.size,sort:ws.sort});
 for(const [key,value] of Object.entries(ws.filters))if(value)q.set(key,String(value));
 try{const r=await fetch('/api/completeness/page?'+q,{cache:'no-store'});const data=await r.json();if(!data.ok)throw new Error(data.error);
  ws.result=data;ws.page=data.page;wsRenderPage(data);
 }catch(e){ws.result=null;wsFind('wsSummary').textContent='Current findings unavailable. Run an integrity check to refresh this dataset.';wsFind('wsPager').innerHTML='';wsFind('wsTable').textContent='Could not load findings: '+e.message}}
function wsRenderPage(data){
 const s=data.summary,c=s.completeness,m=s.manifest;
 for(const g of data.groups)for(const id of g.issue_ids)if(!(g.repair_issue_ids||[]).includes(id))ws.selected.delete(id);
 wsFind('wsSummary').innerHTML='<strong>Unresolved blocking review groups: '+wsEsc(data.overall_counts?.unresolved_groups||0)+' · Confirmed groups: '+wsEsc(data.overall_counts?.confirmed_groups||0)+
 ' · Research allowed: '+(s.research_allowed?'YES':'NO')+'</strong> · Last completed check: '+wsEsc(s.timestamp||'—')+
 ' · '+wsEsc(data.total_groups)+' review groups / '+wsEsc(data.total_findings)+' underlying findings'+
 (!s.research_allowed?'<div class="bad">Blocked by: '+(data.blocking_reasons||[]).map(r=>wsEsc(r.scope+' · '+r.component+' · '+r.start+(r.end!==r.start?'–'+r.end:'')+' · '+r.status+' · '+r.explanation+' ['+r.issue_id+']')).join('<br>')+'</div>':'')+
 (s.freshness?.expected_session?'<p>Freshness: raw '+wsEsc(s.freshness.raw_latest)+' ('+wsEsc(s.freshness.raw_session_lag)+' sessions behind) · adjusted '+wsEsc(s.freshness.adjusted_latest)+' ('+wsEsc(s.freshness.adjusted_session_lag)+' sessions behind) · latest expected completed session '+wsEsc(s.freshness.expected_session)+' · publication cutoff '+wsEsc(s.freshness.publication_cutoff)+'. '+wsEsc(s.freshness.publication_policy)+'</p>':'');
 wsFind('wsEvidence').textContent=JSON.stringify({scope:c.scope,dataset_id:m.dataset_id,fingerprint:m.dataset_fingerprint,raw_latest:m.raw_price_last_date,adjusted_latest:m.adjusted_price_last_date},null,2);
 const rows=data.groups.map(g=>{
  const primary=g.members[0], checked=(g.repair_issue_ids||[]).some(id=>ws.selected.has(id));
  return '<details class="wsGroup"><summary><input class="wsSelect" type="checkbox" data-group="'+wsEsc(g.group_id)+'" '+(checked?'checked':'')+((g.repair_issue_ids||[]).length?'':' disabled title="No data repair required"')+'> '+
   wsEsc(primary.scope_label||primary.ticker)+' · '+wsEsc(primary.date)+' → '+wsEsc(primary.end_date)+' · '+wsEsc(primary.classification)+
   ' · '+wsEsc(primary.resolution_status)+' · '+g.members.length+' component finding(s)</summary>'+
   '<table><tr><th>Issue</th><th>Component</th><th>Severity</th><th>Status</th><th>Reason / evidence</th><th>Action</th></tr>'+
   g.members.map(r=>'<tr><td>'+wsEsc(r.issue_id)+'</td><td>'+wsEsc(r.affected_component)+'</td><td>'+wsEsc(r.severity)+'</td><td>'+wsEsc(r.resolution_status)+'</td><td>'+wsEsc(r.explanation||r.evidence_source)+
    (r.inherited_from?' · inherited from '+wsEsc(r.inherited_from):'')+'</td><td>'+
    (r.resolution_status==='CONFIRMED'?'<button data-action="reopen" data-id="'+wsEsc(r.reopen_issue_id||r.issue_id)+'">Reopen</button>':r.overridable?'<button data-action="confirm" data-id="'+wsEsc(r.issue_id)+'">Acknowledge with reason</button> '+(r.repair_eligible?'<button data-action="repair" data-id="'+wsEsc(r.issue_id)+'">Repair</button>':''):wsEsc(r.action_label||'Inspect details; automatic repair unavailable'))+
    '</td></tr>').join('')+'</table></details>';
 }).join('');
 wsFind('wsTable').innerHTML=rows||'<p>No findings match these filters.</p>';
 for(const box of wsFind('wsTable').querySelectorAll('.wsSelect'))box.onchange=()=>{const g=data.groups.find(x=>x.group_id===box.dataset.group);for(const id of g.repair_issue_ids||[]){if(box.checked)ws.selected.add(id);else ws.selected.delete(id)}wsSelectionStatus()};
 for(const button of wsFind('wsTable').querySelectorAll('button[data-action]'))button.onclick=()=>wsIndividual(button.dataset.action,button.dataset.id);
 wsFind('wsPager').innerHTML='<button id="wsFirst">First</button> <button id="wsPrev">Previous</button> Showing '+data.from+'–'+data.to+' of '+data.total_groups+
 ' groups · page '+data.page+' / '+data.pages+' <button id="wsNext">Next</button> <button id="wsLast">Last</button> '+
 '<label>Groups per page <select id="wsSize">'+[25,50,100,250].map(n=>'<option '+(n===ws.size?'selected':'')+'>'+n+'</option>').join('')+'</select></label> '+
 '<button id="wsSelectPage">Select current page</button> <button id="wsClear">Clear selection</button> · '+ws.selected.size+' underlying findings selected';
 for(const [id,p] of [['wsFirst',1],['wsPrev',data.page-1],['wsNext',data.page+1],['wsLast',data.pages]])wsFind(id).onclick=()=>{ws.page=Math.max(1,Math.min(data.pages,p));wsLoadPage()};
 wsFind('wsSize').onchange=e=>{ws.size=Number(e.target.value);ws.page=1;wsLoadPage()};
 wsFind('wsSelectPage').onclick=()=>{for(const g of data.groups)for(const id of g.repair_issue_ids||[])ws.selected.add(id);wsRenderPage(data)};
 wsFind('wsClear').onclick=()=>{ws.selected.clear();wsRenderPage(data)};
 wsSelectionStatus();
}
async function wsIndividual(action,id){if(ws.busy)return;
 if(action==='repair'){ws.selected=new Set([id]);return wsRepair(false)}
 let body={universe:wsUniverse(),issue_id:id},path='/api/completeness/revoke';
 if(action==='confirm'){const explanation=prompt('Written reason for confirmation:','');if(!explanation?.trim())return;
  body={...body,category:'MANUALLY_ACCEPTED_GAP',explanation,reference:prompt('Supporting reference (optional):','')||''};path='/api/completeness/acknowledge'}
 ws.started=Date.now();wsBusy(true);wsMessage(action,'RUNNING','Saving decision and revalidating.');
 try{await wsPost(path,body);await wsLoadPage();wsMessage(action,'COMPLETED','Decision saved and findings refreshed.')}catch(e){wsMessage(action,'FAILED',e.message)}finally{wsBusy(false)}
}
async function wsExport(scope){if(ws.busy)return;ws.started=Date.now();wsBusy(true);wsMessage('Export','PREPARING_WORKBOOK','Preparing workbook.');
 try{const r=await wsPost('/api/completeness/export-job',{universe:wsUniverse(),scope,page:ws.page,size:ws.size,sort:ws.sort,filters:ws.filters});wsWatch(r.job)}
 catch(e){wsBusy(false);wsMessage('Export','FAILED',e.message)}}
async function wsUpload(){if(ws.busy)return;const file=wsFind('wsFile').files?.[0];if(!file){wsMessage('Upload','FAILED','Choose a .xlsx workbook first.');return}
 let started=false;ws.started=Date.now();wsBusy(true);wsMessage('Upload','READING_FILE',file.name+' · '+file.size.toLocaleString()+' bytes');
 try{if(file.size>12000000)throw new Error('Workbook exceeds the 12 MB limit.');const bytes=new Uint8Array(await file.arrayBuffer());
  wsMessage('Upload','VALIDATING','Workbook read; parsing rows and validating decisions.');let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));
  const response=await wsPost('/api/completeness/import-job',{universe:wsUniverse(),workbook:btoa(raw),filename:file.name});started=true;wsWatch(response.job);
 }catch(e){wsMessage('Upload','FAILED',e.message)}finally{if(!started)wsBusy(false)}
}
function wsRenderPreview(){const p=ws.preview;if(!p)return;
 const counts={valid:p.valid_count||0,rejected:p.counts?.REJECTED||0,unchanged:p.counts?.UNCHANGED||0,confirm:p.counts?.confirm||0,reopen:p.counts?.reopen||0,repair:p.counts?.repair||0,already_applied:p.counts?.ALREADY_APPLIED||0};
 const total=p.total_rows??p.decisions.length,pages=Math.max(1,Math.ceil(total/ws.previewSize));ws.previewPage=Math.min(ws.previewPage,pages);
 const from=(ws.previewPage-1)*ws.previewSize,rows=p.decisions;
 wsFind('wsPreview').innerHTML='<p><strong>'+wsEsc(p.filename||'Reviewed workbook')+'</strong> · '+total+' rows · '+counts.valid+' valid · '+counts.rejected+' rejected · '+counts.unchanged+' unchanged · '+counts.confirm+' confirmations · '+counts.reopen+' reopens · '+counts.repair+' repairs · '+wsEsc(p.inherited_count||0)+' inherited adjusted findings</p>'+
  '<p>File uploaded and validated. No production data or review status has changed. '+(counts.valid?'Review the decisions, then click Apply.':'No decisions can be applied because no valid actionable rows exist. Select confirm, repair, or reopen in the Requested action column and re-upload.')+'</p>'+
  '<button id="wsApply" '+(!counts.valid||ws.busy?'disabled':'')+'>Apply reviewed decisions</button><table><tr><th>Row / group</th><th>Action</th><th>Reason</th><th>Status</th><th>Details</th></tr>'+
  rows.map(d=>'<tr><td>'+wsEsc(d.row)+' · '+wsEsc(d.group_id)+'</td><td>'+wsEsc(d.action)+'</td><td>'+wsEsc(d.reason)+'</td><td>'+wsEsc(d.status)+'</td><td>'+wsEsc(d.error||JSON.stringify(d.mismatches||d.inherited||[]))+'</td></tr>').join('')+'</table>'+
  '<p>Showing '+(total?from+1:0)+'–'+Math.min(from+ws.previewSize,total)+' of '+total+
  ' preview rows · <button id="wsPreviewPrev">Previous</button> '+ws.previewPage+' / '+pages+' <button id="wsPreviewNext">Next</button> <select id="wsPreviewSize">'+[25,50,100,250].map(n=>'<option '+(n===ws.previewSize?'selected':'')+'>'+n+'</option>').join('')+'</select></p>';
 wsFind('wsApply').onclick=()=>wsApply();wsFind('wsPreviewPrev').onclick=()=>wsLoadPreview(Math.max(1,ws.previewPage-1));
 wsFind('wsPreviewNext').onclick=()=>wsLoadPreview(Math.min(pages,ws.previewPage+1));
 wsFind('wsPreviewSize').onchange=e=>{ws.previewSize=Number(e.target.value);wsLoadPreview(1)};
}
async function wsLoadPreview(number){if(!ws.preview)return;ws.previewPage=number;
 const response=await fetch('/api/completeness/preview?'+new URLSearchParams({universe:wsUniverse(),build_id:wsBuild(),id:ws.preview.preview_id,page:number,size:ws.previewSize}),{cache:'no-store'});
 const result=await response.json();if(!result.ok){wsMessage('Preview','FAILED',result.error||'Could not load preview page.');return}
 ws.preview=result;wsRenderPreview();}
async function wsApply(){if(ws.busy||!ws.preview?.valid_count)return;
 ws.started=Date.now();wsBusy(true);wsMessage('Review apply','QUEUED','Click received; creating operation.');
 try{const r=await wsPost('/api/completeness/apply-review',{universe:wsUniverse(),preview_id:ws.preview.preview_id});wsWatch(r.job)}
 catch(e){wsBusy(false);wsMessage('Review apply','FAILED',e.message)}}
async function wsRepair(all){if(ws.busy){wsMessage('Repair','WAITING','Another integrity operation is already running.');return}if(!all&&!ws.selected.size){wsMessage('Repair','FAILED','Select at least one finding.');return}
 const count=ws.selected.size;ws.started=Date.now();wsBusy(true);wsMessage('Repair','QUEUED',all?'Click received; planning all unresolved eligible findings.':'Click received; planning '+count+' selected finding'+(count===1?'':'s')+'.');
 try{const r=await wsPost('/api/completeness/repair-batch',{universe:wsUniverse(),issue_ids:[...ws.selected],all_eligible:all});wsWatch(r.job)}
 catch(e){wsBusy(false);wsMessage('Repair','FAILED',e.message)}}
async function wsDuplicate(){if(ws.busy)return;ws.started=Date.now();wsBusy(true);wsMessage('Duplicate removal','RUNNING','Checking duplicate keys and staging changes.');
 try{await wsPost('/api/repair-safe',{universe:wsUniverse()});await wsLoadPage();wsMessage('Duplicate removal','COMPLETED','Identical duplicates removed; report refreshed.')}
 catch(e){wsMessage('Duplicate removal','FAILED',e.message)}finally{wsBusy(false)}}
function wsWatch(initial){if(initial.metadata?.universe&&(initial.metadata.universe!==wsUniverse()||(initial.metadata.build_id||'')!==wsBuild())){wsMessage('Integrity','OTHER_DATASET','An operation belongs to another dataset. Select its universe and retained build to follow it.');return}ws.job=initial;ws.started=Date.parse(initial.started_at)||Date.now();wsBusy(true);
 if(ws.timer)clearTimeout(ws.timer);
 const poll=async()=>{try{const response=await fetch('/api/completeness/bulk-status',{cache:'no-store'}),body=await response.json(),job=body.job;
  if(job.id!==initial.id)throw new Error('Another operation replaced this job. Recheck the integrity report.');
  wsMessage(job.kind||'Integrity',job.stage||job.status,job.message||'Working…',job);
  if(job.status==='running'){ws.timer=setTimeout(poll,700);return}
  wsBusy(false);if(job.status==='failed')throw new Error(job.error||'Operation failed.');
  if(job.kind==='export'){const r=job.result;const link=document.createElement('a');link.href='/api/completeness/workbook?'+new URLSearchParams({universe:wsUniverse(),build_id:wsBuild(),id:r.export_id});link.download=r.filename;link.click();
   wsMessage('Export','DOWNLOAD_READY','Workbook prepared: '+r.groups+' review groups / '+r.findings+' findings. Browser download requested.',job)}
  else if(job.kind==='review upload'){
    ws.preview=job.result;ws.previewPage=1;sessionStorage.setItem('momentumIntegrityPreview',JSON.stringify({id:ws.preview.preview_id,universe:wsUniverse(),build_id:wsBuild()}));wsRenderPreview();
    wsMessage('Upload',ws.preview.valid_count?'AWAITING_APPLY':'COMPLETED_WITH_WARNINGS',ws.preview.total_rows+' rows read · '+ws.preview.valid_count+' valid actions · '+(ws.preview.counts?.REJECTED||0)+' rejected. No production data or review status changed.',job);
  }
  else {if(job.kind==='publish retained build'){wsFind('wsBuild').value='';await wsLoadBuilds();await wsCheck();return}await wsLoadPage();if(job.kind==='review apply'){
    const ds=job.result?.decisions||[],counts={applied:ds.filter(d=>d.status==='APPLIED').length,rejected:ds.filter(d=>d.status==='REJECTED').length,unchanged:ds.filter(d=>d.status==='UNCHANGED').length};
    wsFind('wsPreview').innerHTML='<h4>Apply result</h4><p>'+wsEsc(JSON.stringify(counts))+'</p><details><summary>Per-row results</summary><pre>'+wsEsc(JSON.stringify(ds,null,2))+'</pre></details>';
    ws.preview=null;sessionStorage.removeItem('momentumIntegrityPreview');wsMessage('Review apply',counts.applied&&counts.rejected===0?'COMPLETED':'COMPLETED_WITH_WARNINGS',counts.applied+' applied · '+counts.rejected+' rejected · '+counts.unchanged+' unchanged. Findings refreshed.',job);
   }else if(job.kind==='repair'){
    const result=job.result||{},rows=result.results||[];wsFind('wsPreview').innerHTML='<h4>Repair results</h4><p>Committed: '+wsEsc(Boolean(result.committed))+' · '+rows.length+' issues inspected</p><pre>'+wsEsc(JSON.stringify(rows,null,2))+'</pre>';
    wsMessage('Repair',result.committed?'COMPLETED':'COMPLETED_WITH_WARNINGS',rows.length+' issue results; '+(result.error||'report refreshed.'),job);
   }else wsMessage(job.kind||'Integrity','COMPLETED','Report refreshed.',job)}
 }catch(e){wsBusy(false);wsMessage(initial.kind||'Integrity','FAILED',e.message)}};
 ws.timer=setTimeout(poll,100);
}
async function wsResume(){try{const saved=JSON.parse(sessionStorage.getItem('momentumIntegrityPreview')||'null');
 if(saved?.universe===wsUniverse()&&(saved.build_id||'')===wsBuild()){const response=await fetch('/api/completeness/preview?'+new URLSearchParams({universe:saved.universe,build_id:saved.build_id||'',id:saved.id}),{cache:'no-store'}),preview=await response.json();
  if(preview.ok){ws.preview=preview;wsRenderPreview()}}
 const response=await fetch('/api/completeness/bulk-status',{cache:'no-store'}),body=await response.json();
 if(body.job?.status==='running')wsWatch(body.job);else if(body.job?.id){ws.started=Date.parse(body.job.started_at)||Date.now();wsMessage(body.job.kind,body.job.stage,body.job.message||body.job.error||'Last operation finished.',body.job)}}catch(_){}}
// Existing file-location and research controls still call these public functions.
// Only explicit check controls start the background full-validation job.
// checkIntegrity(universe, mode) retains its report-returning API contract.
runIntegrityCheck=wsCheck;revalidateCompleteness=wsCheck;
renderIntegrity=()=>wsLoadPage();
document.addEventListener('DOMContentLoaded',wsInit);
if(document.readyState!=='loading')wsInit();

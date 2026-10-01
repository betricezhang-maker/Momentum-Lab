/* Bounded DOM rendering; underlying rows and export precision are unchanged. */
const runtimeTables=new Map();
function clearPagedTables(scope){for(const key of runtimeTables.keys())if(key===scope||key.startsWith(scope+'-'))runtimeTables.delete(key);}
function pagedTable(key,rows,render,size=50){
 if(!/^[a-zA-Z0-9_-]+$/.test(key))throw new Error('Invalid table key');
 if(rows.length<=size){runtimeTables.delete(key);return render(rows,0);}
 runtimeTables.set(key,{rows,render,size});return `<div id="paged-${key}">${pagedTableBody(key,0)}</div>`;
}
function pagedTableBody(key,offset){
 const t=runtimeTables.get(key);if(!t)return '';
 offset=Math.max(0,Math.min(offset,Math.floor((t.rows.length-1)/t.size)*t.size));
 return `<div class="row"><button type="button" class="secondary" onclick="showTablePage('${key}',${offset-t.size})" ${offset===0?'disabled':''}>Previous</button><span>Rows ${offset+1}–${Math.min(offset+t.size,t.rows.length)} of ${t.rows.length}</span><button type="button" class="secondary" onclick="showTablePage('${key}',${offset+t.size})" ${offset+t.size>=t.rows.length?'disabled':''}>Next</button></div>`+t.render(t.rows.slice(offset,offset+t.size),offset);
}
function showTablePage(key,offset){const node=document.getElementById('paged-'+key);if(node)node.innerHTML=pagedTableBody(key,offset);}
async function clearRuntimeCache(){
 const status=document.getElementById('runtimeCacheMessage');status.textContent='Clearing runtime caches…';
 try{const r=await api('/api/clear-runtime-cache',{});status.textContent=r.message;}catch(e){status.textContent=e.message;}
}
let calculationTimer=null;
function calculationBusy(active,message='Working…'){
 if(calculationTimer){clearInterval(calculationTimer);calculationTimer=null;}
 busy(active,message);document.getElementById('busy').classList.toggle('calculation-mode',active);
 for(const button of document.querySelectorAll('[onclick="runResearch()"],[onclick="runStrategyGrid()"],#auditRun'))button.disabled=active;
 if(active)calculationTimer=setInterval(async()=>{
  try{const r=await api('/api/calculation-status');if(calculationTimer&&r.running)document.getElementById('busyText').textContent=r.stage;}catch(e){/* Original request reports any error. */}
 },1500);
}

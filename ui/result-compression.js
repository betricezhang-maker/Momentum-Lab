let resultCompressionToken=null,resultCompressionBusy=false;
const resultMB=n=>(Number(n)/1048576).toFixed(1)+' MB';
function compressionButtons(busy){resultCompressionBusy=busy;$('compressionPreview').disabled=busy;$('compressionDays').disabled=busy;$('compressionApply').disabled=busy||!resultCompressionToken;}
async function previewResultCompression(){
 if(resultCompressionBusy)return;resultCompressionToken=null;compressionButtons(true);$('compressionStatus').textContent='Scanning completed older run folders…';$('compressionList').textContent='';
 try{const r=await api('/api/results-compression/preview',{days:Number($('compressionDays').value)});
 resultCompressionToken=r.supported&&r.file_count?r.token:null;
 $('compressionStatus').textContent=r.supported?`${r.runs.length} older runs · ${r.file_count} uncompressed files · ${resultMB(r.logical_bytes)} logical size. Savings are measured after compression.`:'Windows NTFS compression is required.';
 $('compressionList').textContent=r.runs.map(x=>`${x.run} · ${x.files} files · ${resultMB(x.logical_bytes)}`).join('\n');
 }catch(e){$('compressionStatus').textContent='Preview failed: '+e.message;}finally{compressionButtons(false);}
}
async function compressOlderResults(){
 if(resultCompressionBusy||!resultCompressionToken)return;const token=resultCompressionToken;resultCompressionToken=null;compressionButtons(true);$('compressionStatus').textContent='Compression request received…';
 try{const r=await api('/api/results-compression/apply',{token});watchResultCompression(r.job.id);}catch(e){$('compressionStatus').textContent='Could not start: '+e.message;compressionButtons(false);}
}
async function watchResultCompression(id){
 try{const r=await api('/api/completeness/bulk-status'),j=r.job;
 if(j.kind!=='compress older results'||(id&&j.id!==id)){if(id)$('compressionStatus').textContent='Operation changed; check the saved compression audit.';compressionButtons(false);return;}
 if(j.status==='running'){compressionButtons(true);$('compressionStatus').textContent=`${j.stage} · ${j.current||0}/${j.total??'?'} files · operation ${j.id} · ${j.message||''}`;setTimeout(()=>watchResultCompression(j.id),1500);return;}
 if(j.status==='failed')$('compressionStatus').textContent='Compression failed: '+j.error;
 else {const x=j.result||{};$('compressionStatus').textContent=`${x.status==='partial'?'Partial completion':'Completed'} · ${x.compressed||0} files compressed · ${x.skipped||0} skipped · space saved: ${resultMB(x.bytes_saved||0)}. Paths and contents preserved.`+(x.errors?.length?' Issues: '+x.errors.slice(0,5).join('; '):'');}
 compressionButtons(false);
 }catch(e){$('compressionStatus').textContent='Status unavailable: '+e.message+'. Use Refresh compression status; do not resubmit.';compressionButtons(false);}
}

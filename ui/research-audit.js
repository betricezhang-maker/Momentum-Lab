/* Display production traces. Never calculate MOM or ranks in the browser. */
function auditValue(v){return escapeHtml(v==null?'UNAVAILABLE':typeof v==='boolean'?(v?'YES':'NO'):typeof v==='object'?JSON.stringify(v):typeof v==='number'&&!Number.isInteger(v)?v.toPrecision(15):String(v));}
function auditTable(title,caption,rows,columns){
 return pagedTable('audit-'+title.replace(/[^a-zA-Z0-9]/g,'-'),rows,page=>auditTablePage(title,caption,page,columns));
}
function auditTablePage(title,caption,rows,columns){
 return `<div class="card"><h3>${escapeHtml(title)}</h3><div class="table-scroll"><table><caption>${escapeHtml(caption)}</caption><thead><tr>${columns.map(([key,label])=>`<th>${escapeHtml(label)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${columns.map(([key])=>`<td>${auditValue(r[key])}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${columns.length}">No rows</td></tr>`}</tbody></table></div></div>`;
}
function auditProperties(title,object,caption='Values are retained production outputs; unavailable evidence is not inferred.'){
 return auditTable(title,caption,Object.entries(object).map(([k,v])=>({field:k.replaceAll('_',' '),value:v})),[['field','Field'],['value','Value']]);
}
function renderResearchAudit(a){
 clearPagedTables('audit');
 const integrity=a.integrity||{};
 let h=auditProperties('Dataset Integrity',{status:integrity.status,warnings:integrity.warnings||integrity.notes||[],errors:integrity.errors||[]});
 h+=`<details><summary>Full dataset integrity evidence</summary>${auditProperties('Integrity Details',integrity)}</details>`;
 if(a.blocked){$('researchAuditResult').innerHTML=h;return;}
 h+=auditProperties('Calculation Summary',Object.fromEntries(['universe','signal_date','execution_date','momentum_lookback','eligible_count','valid_score_count','formula','observation_rule','sd_rule','rank_rule','percentile_rule','weight_check'].map(k=>[k,a.manifest[k]])));
 if(a.trace){
  h+=auditProperties('Ticker Calculation / Historical Eligibility',a.trace.summary);
  h+=auditTable('Exact Price Inputs','N+1 ticker observations ending on the exact signal date; adjusted close, full-precision values in export.',a.trace.price_inputs,[['observation','Observation'],['trade_date','Trade Date'],['adjusted_close','Adjusted Close']]);
  h+=auditTable('Daily Returns','Simple returns: adjusted close / previous observed adjusted close − 1. No missing-value fill. SD uses ddof=1.',a.trace.daily_returns,[['return_number','Return #'],['from_date','From Date'],['to_date','To Date'],['daily_return','Daily Return (decimal)']]);
 }
 h+=auditTable('Ranking / Top10 Cutoff','Descending production rank. Exact ties use first-row order; near ties use rtol=1e-10 / atol=1e-12 for diagnostics only. Percentile is an ascending fraction.',a.ranking,[['rank','Rank'],['ticker','Ticker'],['name','Name'],['mom_score','MOM Score'],['percentile','Percentile'],['eligible','Eligible'],['top10','Top10'],['exact_ties','Exact Ties'],['near_ties','Near Ties']]);
 h+=auditTable('Top10 Model Target','Equal weights over the actual selected count; execution-day missing prices may cause a backtest mismatch below.',a.top10_target,[['ticker','Ticker'],['rank','Rank'],['mom_score','MOM Score'],['weight','Target Weight (fraction)']]);
 h+=auditTable('Research Target Consistency','Reference: production signal-date Top10. MATCH compares targets; same dataset is reported separately. UNAVAILABLE means no exact saved target or execution evidence.',a.comparisons,[['source','Source'],['run_id','Run ID'],['status','Status'],['same_dataset','Same Dataset'],['reason','Reason'],['missing_from_candidate','Missing Tickers'],['extra_in_candidate','Extra Tickers'],['rank_mismatches','Rank Differences'],['weight_mismatches','Weight Differences'],['execution_date','Execution Date'],['execution_match','Execution Match']]);
 h+=`<details><summary>Historical universe snapshot, exclusions and all calculation summaries</summary>`;
 h+=auditTable('Universe Snapshot','Latest saved eligibility snapshot on or before the signal date. No future membership snapshot is used.',a.universe_snapshot,Object.keys(a.universe_snapshot[0]||{con_code:null,trade_date:null}).map(k=>[k,k.replaceAll('_',' ')]));
 h+=auditTable('Excluded Securities','Coverage is limited to tickers present in local historical prices or the point-in-time eligibility snapshot. No claim is made about unavailable securities.',a.excluded_securities,[['ticker','Ticker'],['reasons','Exclusion Reasons'],['missing_dates','Missing Exchange Dates']]);
 h+=auditTable('MOM Calculation Observations','Exact retained production values; missing dates are diagnostic and do not change the ticker-observation window.',a.mom_calculation,[['ticker','Ticker'],['price_observations','Prices'],['return_observations','Valid Returns'],['expected_prices','Expected Prices'],['expected_returns','Expected Returns'],['lookback_return','Lookback Return'],['daily_sd','Sample Daily SD'],['sqrt_n','sqrt(N)'],['scaled_volatility','Scaled Volatility'],['mom_score','MOM Score'],['rank','Rank']]);
 h+='</details>';
 h+=`<details><summary>Data Provenance / Audit Manifest</summary>${auditProperties('Data Provenance',a.manifest)}</details>`;
 h+=`<div class="card"><a class="primary" href="/file?path=${encodeURIComponent(a.export_path)}" download="research_audit.zip">Export Research Audit Package</a><p>Immutable package for audit ${escapeHtml(a.manifest.audit_id)}. CSV numbers use 17 significant digits; returns and weights are fractions. Excel: STDEV.S(return range), SQRT(N), last price / first price − 1. Use unrounded MOM values and the documented tie rule.</p></div>`;
 $('researchAuditResult').innerHTML=h;
}
async function runResearchAudit(event){
 event.preventDefault();const button=$('auditRun');button.disabled=true;clearPagedTables('audit');$('researchAuditResult').innerHTML='';
 calculationBusy(true,'Validating audit inputs…');
 $('researchAuditMessage').textContent='Reading production inputs and validating the dataset. A cold dataset can take several minutes.';
 try{
  const a=await api('/api/research-audit',{universe:$('auditUniverse').value,signal_date:$('auditSignal').value,lookback:Number($('auditLookback').value),ticker:$('auditTicker').value.trim(),mode:$('auditMode').value});
  renderResearchAudit(a);$('researchAuditMessage').textContent=a.blocked?a.error:'Audit complete. Export refers to this exact audit, even if data changes later.';
 }catch(e){$('researchAuditMessage').textContent=`Audit failed: ${e.message}`;}finally{calculationBusy(false);button.disabled=false;}
}

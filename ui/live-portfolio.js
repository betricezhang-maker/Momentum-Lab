let liveDetail=null;
// Display filters never change the saved positions, history, or exported rows.
function currentPositionRows(rows){return rows.filter(r=>Number(r.quantity)!==0||Number(r.target_weight)>0);}
const positionHistoryViews=new Map();
const historyFields={all:'All fields',date:'Date',ticker:'Ticker',name:'ETF name',action:'Action',target_weight:'Target weight',weight:'Actual/Paper weight',quantity:'Quantity',price:'Price',position_value:'Position value',source:'Source'};
function historyView(id){if(!positionHistoryViews.has(id))positionHistoryViews.set(id,{field:'all',query:'',page:0});return positionHistoryViews.get(id);}
function historyFieldValue(row,field){return ['target_weight','weight'].includes(field)?pct(row[field]):String(row[field]??'');}
function filteredPositionHistory(rows,state){
 const query=state.query.trim().toLocaleLowerCase();
 return rows.filter(row=>!query||(state.field==='all'?Object.keys(historyFields).filter(k=>k!=='all'):[state.field]).some(field=>historyFieldValue(row,field).toLocaleLowerCase().includes(query)));
}
function positionHistoryBody(d){
 const state=historyView(d.portfolio.id),rows=filteredPositionHistory(d.position_history||[],state),pages=Math.max(1,Math.ceil(rows.length/10));
 state.page=Math.max(0,Math.min(state.page,pages-1));const start=state.page*10;
 const controls=`<div class="row"><button class="secondary" onclick="positionHistoryPage(0)" ${state.page===0?'disabled':''}>First</button><button class="secondary" onclick="positionHistoryPage(${state.page-1})" ${state.page===0?'disabled':''}>Previous</button><span>Showing ${rows.length?start+1:0}–${Math.min(start+10,rows.length)} of ${rows.length} · Page ${state.page+1} of ${pages} · 10 rows per page</span><button class="secondary" onclick="positionHistoryPage(${state.page+1})" ${state.page===pages-1?'disabled':''}>Next</button><button class="secondary" onclick="positionHistoryPage(${pages-1})" ${state.page===pages-1?'disabled':''}>Last</button></div>`;
 return controls+'<div class="table-scroll"><table><caption>Historical target and ledger timeline. Values in RMB; weights shown as percentages. Filtering affects this view only.</caption><tr><th>Date</th><th>Ticker / Name</th><th>Action</th><th>Target Weight</th><th>Actual/Paper Weight</th><th>Quantity</th><th>Price (RMB)</th><th>Position Value (RMB)</th><th>Source</th></tr>'+rows.slice(start,start+10).map(r=>`<tr><td>${escapeHtml(r.date)}</td><td>${escapeHtml(r.ticker)}<br>${escapeHtml(r.name)}</td><td>${escapeHtml(r.action)}</td><td>${pct(r.target_weight)}</td><td>${pct(r.weight)}</td><td>${money(r.quantity)}</td><td>${money(r.price)}</td><td>${money(r.position_value)}</td><td>${escapeHtml(r.source)}</td></tr>`).join('')+(rows.length?'':'<tr><td colspan="9">No matching position history.</td></tr>')+'</table></div>';
}
function positionHistoryPanel(d){
 const state=historyView(d.portfolio.id);
 return `<div class="card"><h3>Position History</h3><div class="row"><div><label for="positionHistoryField">Filter field</label><select id="positionHistoryField" onchange="filterPositionHistory()">${Object.entries(historyFields).map(([key,label])=>`<option value="${key}" ${state.field===key?'selected':''}>${label}</option>`).join('')}</select></div><div><label for="positionHistoryQuery">Contains (dates: YYYY-MM-DD)</label><input id="positionHistoryQuery" value="${escapeHtml(state.query)}" oninput="filterPositionHistory()" placeholder="Ticker, name, SELL, PAPER…"></div><button class="secondary" onclick="clearPositionHistoryFilter()">Clear filter</button></div><div id="positionHistoryRows">${positionHistoryBody(d)}</div></div>`;
}
function filterPositionHistory(){const state=historyView(liveDetail.portfolio.id);Object.assign(state,{field:$('positionHistoryField').value,query:$('positionHistoryQuery').value,page:0});$('positionHistoryRows').innerHTML=positionHistoryBody(liveDetail);}
function clearPositionHistoryFilter(){$('positionHistoryField').value='all';$('positionHistoryQuery').value='';filterPositionHistory();}
function positionHistoryPage(page){historyView(liveDetail.portfolio.id).page=page;$('positionHistoryRows').innerHTML=positionHistoryBody(liveDetail);}
let liveTradeCorrection=null;
let liveTradeRequest=null;
let rebalanceRequest=null;
let historicalPreviewKey=null;
let rebalanceCapital={capital_adjustment:'NONE',capital_amount:0};
let paperApplyPending=false;
let paperOperation=null;
function paperFeedback(message){
 paperOperation={id:liveDetail?.portfolio.id,message};
 const panel=$('paperOperationStatus');if(panel)panel.textContent=message;
 $('liveMessage').textContent=message;
}
const localToday=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const money=v=>v==null?'—':Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
function activatePage(id){document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.page===id));document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===id));}
async function formAction(event,errorId,action){event.preventDefault();const button=event.target.querySelector('[type="submit"]')||event.target.querySelector('button.primary');button.disabled=true;$(errorId).textContent='';try{await action();}catch(e){$(errorId).textContent=e.message;}finally{button.disabled=false;}}

async function createLivePortfolio(event){
 event.preventDefault();const blockers=createLiveBlockers();if(blockers.length){updateCreateLiveState();return;}
 createLivePending=true;
 try{await formAction(event,'createLiveError',async()=>{
 if($('liveStartMode').value==='historical'&&historicalPreviewKey!==currentPreviewKey())throw new Error('Preview the selected historical signal before creating the portfolio.');
 const result=await api('/api/live/create',{...pendingLiveSource,name:$('liveName').value,start_date:$('liveStart').value,start_date_type:$('liveStartDateType').value,end_date:$('liveEnd').value,start_mode:$('liveStartMode').value,cost_bps:+$('liveCost').value,capital:+$('liveCapital').value,status:$('liveCreateStatus').value});
 $('createLiveDialog').close();activatePage('live');await loadLivePortfolios(result.id);
 });}finally{createLivePending=false;updateCreateLiveState();}
}

async function loadLivePortfolios(selected){
 try{const current=typeof selected==='string'?selected:$('liveSelector').value;const result=await api('/api/live/portfolios?summary=1&view='+encodeURIComponent($('liveView').value));
 $('liveSelector').innerHTML='<option value="">Select a portfolio</option>'+result.portfolios.map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)} · ${escapeHtml(p.status)}</option>`).join('');
 if(result.portfolios.some(p=>p.id===current))$('liveSelector').value=current;
 else if(result.portfolios.length)$('liveSelector').value=result.portfolios[0].id;
 await loadLiveDetail();
 }catch(e){$('liveMessage').textContent=e.message;}
}
let historicalPreviewStatus='NO TARGET FOUND';
let historicalPreviewRequest=0;
let historicalPreviewController=null;
let createLivePending=false;
function currentPreviewKey(){return [$('liveStart').value,$('liveStartDateType').value,$('liveEnd').value,$('liveCost').value,pendingLiveSource?.run_id,pendingLiveSource?.lookback,pendingLiveSource?.selection,pendingLiveSource?.rebalance_days].join('|');}
function createLiveBlockers(){
 const reasons=[];
 if($('liveStartMode').value==='historical'&&(historicalPreviewStatus!=='READY'||historicalPreviewKey!==currentPreviewKey()))reasons.push('Resolve the historical target preview.');
 if(!Number.isFinite(Number($('liveCapital').value))||Number($('liveCapital').value)<=0)reasons.push('Enter starting capital to create portfolio.');
 if(!$('liveName').value.trim())reasons.push('Enter a portfolio name.');
 if(!['PAPER','ACTIVE'].includes($('liveCreateStatus').value))reasons.push('Choose PAPER or ACTIVE tracking.');
 if(!$('liveStart').value)reasons.push('Choose a starting date.');
 if($('liveCost').value===''||!Number.isFinite(Number($('liveCost').value))||Number($('liveCost').value)<0||Number($('liveCost').value)>10000)reasons.push('Enter a valid cost in bps.');
 if(createLivePending)reasons.push('Creating portfolio…');
 return reasons;
}
function updateCreateLiveState(){
 const reasons=createLiveBlockers();$('createLiveSubmit').disabled=!!reasons.length;
 if($('createLiveReasons'))$('createLiveReasons').textContent=reasons.length?reasons.join(' '):'Ready to create portfolio.';
}
function invalidateHistoricalPreview(){
 historicalPreviewRequest++;historicalPreviewController?.abort();historicalPreviewController=null;
 historicalPreviewKey=null;historicalPreviewStatus='NO TARGET FOUND';
 $('historicalPreview').textContent='Preview the selected dates to resolve the target.';
 if($('previewHistoricalBtn'))$('previewHistoricalBtn').disabled=false;
 updateCreateLiveState();
}
function toggleHistoricalInputs(){
 invalidateHistoricalPreview();
 const historical=$('liveStartMode').value==='historical';
 $('historicalEndBox').hidden=!historical;$('liveEnd').required=historical;
 $('liveStartLabel').textContent=historical?'Historical start date (choose its meaning below)':'Start tracking today';
 if(!historical){$('liveStart').value=localToday();$('liveEnd').value=localToday();}
 updateCreateLiveState();
}
async function previewHistoricalTarget(){
 invalidateHistoricalPreview();
 const request=historicalPreviewRequest,key=currentPreviewKey();
 const controller=new AbortController();historicalPreviewController=controller;
 const payload={...pendingLiveSource,start_date:$('liveStart').value,start_date_type:$('liveStartDateType').value,end_date:$('liveEnd').value,cost_bps:+$('liveCost').value};
 let timer;
 historicalPreviewStatus='RESOLVING';updateCreateLiveState();
 if($('previewHistoricalBtn'))$('previewHistoricalBtn').disabled=true;
 $('historicalPreview').textContent='Resolving the saved signal and T+1 execution target… (timeout: 120 seconds)';
 try{
  const result=await Promise.race([
   fetch('/api/live/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal}).then(async response=>{
    const body=await response.json();if(!response.ok||body.ok===false){const error=new Error(body.error||'Preview failed');error.previewStatus=body.preview_status;throw error;}return body;
   }),
   new Promise((_,reject)=>{timer=setTimeout(()=>{const error=new Error('Target resolution timed out after 120 seconds. Retry when other work has finished.');reject(error);controller.abort();},120000);})
  ]);
  if(request!==historicalPreviewRequest||key!==currentPreviewKey())return;
  const checks=result.integrity||{};
  if(checks.research_vs_backtest?.match===false||checks.saved_grid_vs_backtest?.match===false)throw new Error('STRATEGY TARGET MISMATCH');
  if(result.dataset_integrity?.status==='FAIL'){const error=new Error('Dataset integrity FAIL blocks this target.');error.previewStatus='DATASET BLOCKED';throw error;}
  if(!result.holdings?.length||!result.execution_date)throw new Error('NO TARGET FOUND: missing holdings or execution date.');
  historicalPreviewKey=key;historicalPreviewStatus='READY';
  const st=result.strategy||{};
  $('historicalPreview').innerHTML=`<div class="card"><b>READY — Historical Portfolio Preview</b><p>Requested date: ${escapeHtml(result.requested_start_date)}<br>Resolved signal date: ${escapeHtml(result.signal_date)}<br>Execution date: ${escapeHtml(result.execution_date)}<br>${escapeHtml(result.resolution_rule||'')}<br>Strategy: MOM${escapeHtml(st.lookback)} / ${escapeHtml(st.selection)} / ${escapeHtml(st.rebalance_days)}D · ${escapeHtml(st.cost_bps)} bps<br>Target source: ${escapeHtml(checks.target_source||'BACKTEST')}<br>Dataset: ${escapeHtml(result.dataset_integrity?.status||'Validated')}</p><table><caption>Resolved target holdings; weights sum to 100%</caption><tr><th>Rank</th><th>Ticker</th><th>Name</th><th>MOM score</th><th>Target weight</th></tr>${result.holdings.map(r=>`<tr><td>${escapeHtml(r.rank??'—')}</td><td>${escapeHtml(r.ticker)}</td><td>${escapeHtml(r.name)}</td><td>${r.score==null?'—':escapeHtml(Number(r.score).toFixed(6))}</td><td>${pct(r.target_weight)}</td></tr>`).join('')}</table><p>${checks.saved_grid_vs_backtest?'MATCH — saved target and Research Lab agree.':'MATCH — existing backtest engine and Research Lab agree; no exact saved rebalance was available.'}</p></div>`;
 }catch(e){
  if(request!==historicalPreviewRequest)return;
  historicalPreviewKey=null;
  historicalPreviewStatus=e.previewStatus||(/NO TARGET FOUND/.test(e.message)?'NO TARGET FOUND':/INVALID SIGNAL DATE/.test(e.message)?'INVALID SIGNAL DATE':'ERROR');
  $('historicalPreview').textContent=historicalPreviewStatus+': '+e.message+' Run '+payload.run_id+' · requested '+payload.start_date+' · MOM'+payload.lookback+' / '+payload.selection+' / '+payload.rebalance_days+'D. You can retry.';
 }finally{
  clearTimeout(timer);
  if(request===historicalPreviewRequest){historicalPreviewController=null;if($('previewHistoricalBtn'))$('previewHistoricalBtn').disabled=false;updateCreateLiveState();}
 }
}

async function loadLiveDetail(refresh=false){
 const id=$('liveSelector').value;if(!id){clearPagedTables('live');clearPagedTables('events');liveDetail=null;$('liveContent').innerHTML='';return;}
 if(liveDetail?.portfolio.id!==id)rebalanceCapital={capital_adjustment:'NONE',capital_amount:0};
 $('liveMessage').textContent='Loading portfolio and local prices…';
 try{const detail=await api('/api/live/detail',{id,refresh:refresh===true});if($('liveSelector').value!==id)return;rebalanceCapital={capital_adjustment:'NONE',capital_amount:0};liveDetail=detail;renderLive();}
 catch(e){$('liveMessage').textContent=e.message;}
}

function renderLive(){
 clearPagedTables('live');
 const d=liveDetail,p=d.portfolio,s=d.stats,st=p.strategy;
 const wf=d.rebalance_workflow;
 $('liveMessage').textContent=`As of ${d.asof}; local prices through ${d.data_end}. Source research run: ${p.source_run_id}`;
 const kpis=[['Portfolio Status',p.archived?'ARCHIVED':p.status],['Current NAV (RMB)',money(s.current_nav)],['Investment P/L excluding cash flows (RMB)',money(s.investment_profit_loss)],['Cumulative Investment Return',pct(s.cumulative_return)],['External Contributions (RMB)',money(s.external_contributions)],['External Withdrawals (RMB)',money(s.external_withdrawals)],['Cash (RMB)',money(s.cash)],['Invested / Cash',`${pct(s.invested_pct)} / ${pct(s.cash_pct)}`],['Last Signal',wf.last_signal_date||'—'],['Last Rebalance',wf.last_rebalance_date||'—'],['Next Signal',wf.signal_date||'Calendar coverage unavailable'],['Next Rebalance',wf.execution_date||'Calendar coverage unavailable'],['Sessions Until Signal',wf.trading_sessions_until_signal??'—'],['Sessions Until Rebalance',wf.trading_sessions_until??'—'],['Current Model Turnover',pct(wf.model_turnover)],['Initial Funding Turnover',pct(s.initial_funding_turnover)],['Cumulative Recurring Turnover',pct(s.recurring_turnover)],['Cumulative Actual/Paper Turnover',pct(s.total_turnover)],['Median Rebalance Turnover',pct(s.median_turnover)],['Maximum Rebalance Turnover',pct(s.maximum_rebalance_turnover)],['Holdings Retention Rate',pct(s.holdings_retention_rate)],['Days Live',s.days_live],['Opening Ledger Capital (RMB)',money(p.capital)],['Annualized Return',pct(s.annualized_return)],['Annual Volatility',pct(s.annual_volatility)],['Max Drawdown',pct(s.max_drawdown)],['Annualized Turnover',pct(s.annualized_turnover)],['Avg Journal Turnover',pct(s.average_rebalance_turnover)],['Realized P/L (RMB)',money(s.realized_pl)],['Fees Paid (RMB)',money(s.total_fees)],['Recorded Rebalances',s.number_of_rebalances]];
 const readOnly=p.status==='CLOSED'||p.archived;const statuses=p.mode==='PAPER'?['PAPER','PAUSED','CLOSED']:['ACTIVE','PAUSED','CLOSED'];
 let h=`<div class="card"><span class="live-tag">${d.is_paper?'PAPER SIMULATION — no actual capital':'LIVE ACTUAL PERFORMANCE'}</span><h3>${escapeHtml(p.name)} · ${escapeHtml(p.archived?'ARCHIVED':p.status)}</h3><p>${escapeHtml(universeLabel(st.universe))} · MOM${st.lookback} / ${escapeHtml(st.selection)} / ${st.rebalance_days}D · ${st.cost_bps} bps model cost · Calculation start ${p.start_date}${st.requested_start_date?' · Requested '+escapeHtml(st.start_date_type)+' '+escapeHtml(st.requested_start_date):''}${st.mapped_execution_date?' · Mapped execution '+escapeHtml(st.mapped_execution_date):''}${st.historical_end?' → '+st.historical_end:''}</p><div class="live-actions"><button class="primary" onclick="openTrade()" ${readOnly||d.automatic_paper?'disabled':''}>Record Trade</button><select id="liveStatus" aria-label="Portfolio status" ${readOnly?'disabled':''}>${statuses.map(x=>`<option ${x===p.status?'selected':''}>${x}</option>`).join('')}</select><button class="secondary" onclick="changeLiveStatus()" ${readOnly?'disabled':''}>Save Status</button>${d.is_paper&&!readOnly?'<button class="secondary" onclick="openPromotion()">Continue as Active</button>':''}${p.archived?'<button class="secondary" onclick="restoreLive()">Restore</button>':'<button class="secondary" onclick="archiveLive()">Archive</button>'}<button class="secondary" onclick="openDeletePortfolio()">${d.is_paper?'Delete Trial':'Delete Portfolio'}</button><button class="secondary" onclick="exportLive()">Export</button></div><div class="live-grid">${kpis.map(([k,v])=>`<div class="live-kpi">${k}<strong>${escapeHtml(v)}</strong></div>`).join('')}</div></div>`;
 if(d.data_end<localToday())h+=`<p class="live-notice">Prices are not current: latest local data ${d.data_end}. Review quote dates before recording trades.</p>`;
  if(d.calendar_diagnostics){const cd=d.calendar_diagnostics;h+=`<div class="card"><h3>Trading Calendar / Price Coverage</h3><p>${escapeHtml(cd.failure_reason||cd.price_status||'Calendar loaded.')}</p><details><summary>Portable calendar resolution and schedule diagnostics</summary><table><caption>Paths are resolved at runtime from the portable application folder. Calendar coverage is independent of price coverage.</caption><tr><th>Diagnostic</th><th>Value</th></tr>${Object.entries(cd).map(([k,v])=>`<tr><td>${escapeHtml(k.replaceAll('_',' '))}</td><td>${escapeHtml(v==null?'—':v)}</td></tr>`).join('')}</table></details></div>`;}
 h+='<div class="card"><h3>Current Positions and Model Targets</h3><div class="table-scroll"><table><caption>Raw-close valuation in RMB; unrealized P/L includes buy fees in cost basis. Shows held positions and current model targets only; fully exited non-target holdings remain in Position History.</caption><tr><th>Ticker / Name</th><th>Signal Rank</th><th>Target Weight</th><th>Actual Weight</th><th>Qty</th><th>Average Cost</th><th>Current Price</th><th>Quote Date / Source</th><th>P/L (RMB)</th></tr>'+currentPositionRows(d.positions).map(r=>`<tr><td>${escapeHtml(r.ticker)}<br>${escapeHtml(r.name)}</td><td>${r.signal_rank??'—'}</td><td>${pct(r.target_weight)}</td><td>${pct(r.actual_weight)}</td><td>${money(r.quantity)}</td><td>${money(r.average_cost)}</td><td>${money(r.current_price)}</td><td>${escapeHtml(r.price_date||'No quote')}<br>${escapeHtml(r.valuation_source)}</td><td>${money(r.pl)}</td></tr>`).join('')+'</table></div></div>';
 const latest=d.signals.at(-1)?.payload;
 const executable=!paperApplyPending&&(d.automatic_paper?wf.status==='WAITING_FOR_EXECUTION':['WAITING_FOR_EXECUTION','PARTIALLY_EXECUTED'].includes(wf.status));
 const paperReason=readOnly?'Portfolio is closed or archived.':!d.is_paper?'Paper rebalance is available only for PAPER portfolios.':!d.automatic_paper?'This is a manual PAPER portfolio. Use Review / Record Actual Rebalance, or create an automatic PAPER portfolio from Strategy Grid.':!wf.signal_id?'No generated signal target is available yet. Wait for the signal date, then refresh.':wf.status!=='WAITING_FOR_EXECUTION'?`Paper application is not ready. Current workflow status: ${String(wf.status||'UNKNOWN').replaceAll('_',' ')}.`:'';
 h+=`<div class="card ${wf.overdue?'live-notice':''}"><h3>NEXT REBALANCE · ${escapeHtml(wf.status.replaceAll('_',' '))}${wf.overdue?' · OVERDUE':''}</h3><p><b>Next Signal Date:</b> ${escapeHtml(wf.signal_date||'Calendar coverage unavailable')} · <b>Next Execution / Rebalance Date:</b> ${escapeHtml(wf.execution_date||'Calendar coverage unavailable')} · <b>Sessions until signal:</b> ${wf.trading_sessions_until_signal??'—'} · <b>Sessions until execution:</b> ${wf.trading_sessions_until??'—'}</p><p><b>Signal Status:</b> ${escapeHtml(wf.signal_status||(wf.signal_id?'READY':'WAITING'))} · <b>Target Status:</b> ${escapeHtml(wf.target_status||(wf.signal_id?'GENERATED':'NOT YET GENERATED'))}</p><p><b>Strategy:</b> ${escapeHtml(wf.strategy)} · <b>Current NAV:</b> RMB ${money(wf.current_nav)} · <b>Current cash:</b> RMB ${money(wf.current_cash)}</p><div class="row"><div><label for="capitalAdjustment">Capital adjustment before this rebalance</label><select id="capitalAdjustment" ${!executable||readOnly?'disabled':''}><option value="NONE" ${rebalanceCapital.capital_adjustment==='NONE'?'selected':''}>No Change</option><option value="ADD" ${rebalanceCapital.capital_adjustment==='ADD'?'selected':''}>Add Capital</option><option value="WITHDRAW" ${rebalanceCapital.capital_adjustment==='WITHDRAW'?'selected':''}>Withdraw Capital</option></select></div><div><label for="capitalAmount">Amount (RMB)</label><input id="capitalAmount" type="number" min="0" step="0.01" value="${rebalanceCapital.capital_amount||0}" ${!executable||readOnly?'disabled':''}></div><button class="secondary" onclick="refreshRebalancePreview()" ${!executable||readOnly?'disabled':''}>Update Rebalance Preview</button></div><div class="live-grid"><div class="live-kpi">Current NAV (RMB)<strong>${money(wf.current_nav)}</strong></div><div class="live-kpi">Capital Added (RMB)<strong>${money(wf.capital_added)}</strong></div><div class="live-kpi">Capital Withdrawn (RMB)<strong>${money(wf.capital_withdrawn)}</strong></div><div class="live-kpi">Investable NAV (RMB)<strong>${money(wf.investable_nav)}</strong></div><div class="live-kpi">Target Holdings Count<strong>${wf.target_holdings_count}</strong></div><div class="live-kpi">Target Weight per Holding<strong>${pct(wf.target_weight_per_holding)}</strong></div><div class="live-kpi">Model Turnover<strong>${pct(wf.model_turnover)}</strong></div><div class="live-kpi">Estimated Trading Cost (RMB)<strong>${money(wf.estimated_model_cost)}</strong></div><div class="live-kpi">Expected Post-Rebalance Cash (RMB)<strong>${money(wf.expected_post_rebalance_cash)}</strong></div></div><h3>Proposed Rebalanced Portfolio</h3><div class="table-scroll"><table><caption>RMB values use current marked holdings and the investable NAV shown above. A 0.25 percentage-point weight tolerance identifies no-material-change carries.</caption><tr><th>Action</th><th>Ticker</th><th>ETF Name</th><th>Rank / Score</th><th>Current Quantity</th><th>Current Value (RMB)</th><th>Current Weight</th><th>Target Weight</th><th>Target Value (RMB)</th><th>Suggested Trade Value (RMB)</th><th>Suggested Quantity Change</th><th>Final Target Value (RMB)</th><th>Status</th></tr>${wf.recommendations.map(r=>`<tr><td>${escapeHtml(r.action)}</td><td>${escapeHtml(r.ticker)}</td><td>${escapeHtml(r.name)}</td><td>${r.rank??'—'} / ${r.score==null?'—':Number(r.score).toFixed(4)}</td><td>${money(r.current_quantity)}</td><td>${money(r.current_value)}</td><td>${pct(r.current_weight)}</td><td>${pct(r.target_weight)}</td><td>${money(r.target_value)}</td><td>${money(r.suggested_trade_value)}</td><td>${money(r.suggested_quantity_change)}</td><td>${money(r.final_target_value)}</td><td>${escapeHtml(r.status)}</td></tr>`).join('')}</table></div><div class="live-grid"><div class="live-kpi">Current Portfolio Value (RMB)<strong>${money(wf.current_portfolio_value??wf.current_nav)}</strong></div><div class="live-kpi">Capital Added / Withdrawn (RMB)<strong>${money((wf.capital_added||0)-(wf.capital_withdrawn||0))}</strong></div><div class="live-kpi">Investable NAV (RMB)<strong>${money(wf.investable_nav)}</strong></div><div class="live-kpi">Total Sell Value (RMB)<strong>${money(wf.total_sell_value)}</strong></div><div class="live-kpi">Total Buy Value (RMB)<strong>${money(wf.total_buy_value)}</strong></div><div class="live-kpi">Net Cash Change (RMB)<strong>${money(wf.net_cash_change)}</strong></div><div class="live-kpi">Model Turnover<strong>${pct(wf.model_turnover)}</strong></div><div class="live-kpi">Estimated Cost (RMB)<strong>${money(wf.estimated_model_cost)}</strong></div><div class="live-kpi">Expected Cash (RMB)<strong>${money(wf.expected_post_rebalance_cash)}</strong></div></div><h3>Final Portfolio After Rebalance</h3><div class="table-scroll"><table><caption>Target portfolio only; quantities use each row’s displayed reference price and remain suggested until Paper application or confirmed Active fills. Final target values reserve estimated trading cost.</caption><tr><th>Ticker</th><th>ETF Name</th><th>Rank</th><th>Status</th><th>Final Quantity</th><th>Final Target Weight</th><th>Final Target Value (RMB)</th><th>Change vs Previous Portfolio (RMB)</th></tr>${(wf.final_portfolio||[]).map(r=>`<tr><td>${escapeHtml(r.ticker)}</td><td>${escapeHtml(r.name)}</td><td>${r.rank??'—'}</td><td>${escapeHtml(r.status)}</td><td>${money(r.final_quantity)}</td><td>${pct(r.final_target_weight)}</td><td>${money(r.final_target_value)}</td><td>${money(r.change_vs_previous)}</td></tr>`).join('')}</table></div><div class="live-actions">${d.automatic_paper?`<button class="primary" onclick="applyPaperRebalance()" ${!executable||readOnly?'disabled':''}>Apply Paper Rebalance</button>`:`<button class="primary" onclick="openRebalanceReview()" ${!executable||readOnly?'disabled':''}>Review / Record Actual Rebalance</button>`}<button class="secondary" onclick="skipCurrentRebalance()" ${!executable||readOnly?'disabled':''}>Mark Skipped</button></div><p class="small">Signal and execution dates follow the saved trading-session schedule. ACTIVE holdings change only through confirmed trades; model targets remain separate.</p></div>`;
 h+=`<div class="card"><h3>Model vs Actual Portfolio Performance</h3><p>${d.is_paper?'Model vs Paper ledger':'Model vs Actual'} · Model ${pct(s.model_return)} · ${d.is_paper?'Paper':'Actual'} ${pct(s.cumulative_return)} · Implementation gap ${pct(s.implementation_gap)} (percentage points)</p><canvas id="liveChart" class="live-plot" aria-label="Model versus actual NAV chart"></canvas><p class="small">Blue: model adjusted-price NAV, estimated costs. Green: ${d.is_paper?'paper':'actual'} ledger NAV, recorded fees and raw closes. X: date. Y: NAV in RMB.</p></div>`;
 h+=pagedTable('live-journals',d.journals,(page,offset)=>'<div class="card"><h3>Rebalance History</h3><div class="table-scroll"><table><caption>Permanent Paper, actual and continuation records. NAV before excludes the displayed external flow; investable NAV includes it.</caption><tr><th>Rebalance # / Status / Mode</th><th>Signal Date</th><th>Execution / Actual Date</th><th>NAV Before (RMB)</th><th>Contribution / Withdrawal (RMB)</th><th>Investable NAV (RMB)</th><th>Retained / Bought / Sold</th><th>Model / Actual Turnover</th><th>Trading Cost / Fee (RMB)</th><th>NAV After (RMB)</th><th>Details</th></tr>'+page.map((j,i)=>{const r=j.payload;return `<tr><td>#${offset+i+1} · ${escapeHtml(r.status||'COMPLETED')} / ${escapeHtml(r.kind||'ACTUAL')}</td><td>${escapeHtml(r.signal_date||r.date||'—')}</td><td>${escapeHtml(r.execution_date||r.date||'—')}${r.actual_trade_date?' / '+escapeHtml(r.actual_trade_date):''}</td><td>${money(r.nav_before)}</td><td>+${money(r.contribution||0)} / −${money(r.withdrawal||0)}</td><td>${money(r.investable_nav)}</td><td>${r.retained??'—'} / ${r.bought??'—'} / ${r.sold??'—'}</td><td>${pct(r.model_turnover)} / ${pct(r.actual_turnover)}</td><td>${money(r.model_cost)} / ${money(r.fees??r.fees_paid)}</td><td>${money(r.nav_after)}</td><td><details><summary>Holdings / proposal / fills / notes</summary><p>Before: ${escapeHtml(JSON.stringify(r.holdings_before||{}))}<br>Target: ${escapeHtml(JSON.stringify(r.target_holdings||{}))}<br>Proposed: ${escapeHtml(JSON.stringify(r.proposed_trades||[]))}<br>Actual/Paper fills: ${escapeHtml(JSON.stringify(r.actual_trades||r.trade_ids||[]))}<br>After: ${escapeHtml(JSON.stringify(r.holdings_after||{}))}<br>Notes: ${escapeHtml(r.notes||'—')}</p></details></td></tr>`}).join('')+'</table></div></div>');
 const modelHistory=d.model_journals||[];h+=pagedTable('live-model-history',modelHistory,(page,offset)=>'<div class="card"><h3>Historical Model / Paper Rebalances</h3><div class="table-scroll"><table><caption>Same saved targets and one-way turnover engine used by research.</caption><tr><th>Signal → Rebalance</th><th>Before → After</th><th>Retained / Sold / Bought</th><th>Turnover</th><th>Cost (RMB)</th><th>NAV Before / After</th><th>Holdings</th></tr>'+page.map(r=>`<tr><td>${r.signal_date} → ${r.date}</td><td>${r.holdings_before.length} → ${r.holdings_after.length}</td><td>${r.retained} / ${r.sold} / ${r.bought}</td><td>${pct(r.one_way_turnover)}</td><td>${money(r.cost)}</td><td>${money(r.nav_before)} / ${money(r.nav_after)}</td><td><details><summary>KEEP / SELL / BUY</summary>KEEP: ${escapeHtml(r.keep||'—')}<br>SELL: ${escapeHtml(r.sell||'—')}<br>BUY: ${escapeHtml(r.buy||'—')}</details></td></tr>`).join('')+'</table></div></div>');
 h+=positionHistoryPanel(d);
 if(d.reconciliation?.status!=='UNAVAILABLE')h+=`<div class="card ${d.reconciliation.status==='MATCH'?'':'live-notice'}"><h3>${d.reconciliation.status==='MATCH'?'Historical Reconstruction Reconciled':'⚠ Historical Reconstruction Mismatch'}</h3><p>Backtest ending NAV ${money(d.reconciliation.backtest_ending_nav)} · Reconstructed ending NAV ${money(d.reconciliation.reconstructed_ending_nav)} · Difference ${money(d.reconciliation.difference)} · Tolerance ${money(d.reconciliation.tolerance)}</p></div>`;
 if(d.target_integrity){const checks=Object.entries(d.target_integrity).filter(([,v])=>v&&typeof v==='object'&&'match'in v);const ok=checks.every(([,v])=>v.match);h+=`<div class="card ${ok?'':'live-notice'}"><h3>${ok?'Strategy Target Integrity: MATCH':'⚠ STRATEGY TARGET MISMATCH'}</h3><p>Source: ${escapeHtml(d.target_integrity.target_source||'historical research')}</p>${checks.map(([name,v])=>`<details><summary>${escapeHtml(name.replaceAll('_',' '))}: ${v.match?'MATCH':'MISMATCH'} (${v.reference_count} vs ${v.candidate_count})</summary><p>Missing: ${escapeHtml(v.missing_from_candidate.join(', ')||'—')}<br>Extra: ${escapeHtml(v.extra_in_candidate.join(', ')||'—')}<br>Rank mismatches: ${escapeHtml(JSON.stringify(v.rank_mismatches))}<br>Weight mismatches: ${escapeHtml(JSON.stringify(v.weight_mismatches))}</p></details>`).join('')}</div>`;}
 h+=pagedTable('live-flows',d.cash_flows,(page,offset)=>'<div class="card"><h3>External Capital Flow Ledger</h3><div class="table-scroll"><table><caption>Contributions and withdrawals are stored independently and excluded from investment return.</caption><tr><th>Effective Date</th><th>Type</th><th>Amount (RMB)</th><th>Linked Signal</th><th>Recorded UTC</th></tr>'+page.map(f=>`<tr><td>${escapeHtml(f.date)}</td><td>${escapeHtml(f.kind)}</td><td>${money(f.amount)}</td><td>${escapeHtml(f.signal_id||'—')}</td><td>${escapeHtml(f.recorded_at)}</td></tr>`).join('')+'</table></div></div>');
 h+=pagedTable('live-trades',d.trades,(page,offset)=>'<div class="card"><h3>Manual Trade Ledger</h3><div class="table-scroll"><table><caption>OPENING_TRANSFER records preserve Paper quantities and cost basis without counting the continuation as turnover.</caption><tr><th>Date</th><th>Ticker</th><th>Action</th><th>Price / Dividend</th><th>Qty / Split Ratio</th><th>Fee</th><th>Notes</th><th>Correction</th></tr>'+page.map((t,i)=>`<tr><td>${t.date}</td><td>${escapeHtml(t.ticker)}</td><td>${t.side}</td><td>${money(t.price)}</td><td>${money(t.quantity)}</td><td>${money(t.fee)}</td><td>${escapeHtml(t.notes)}</td><td><button class="secondary" onclick="openTrade(${offset+i})" ${readOnly||t.side==='OPENING_TRANSFER'?'disabled':''}>Correct</button></td></tr>`).join('')+'</table></div></div>');
 h+=`<div class="card small">${d.notes.map(escapeHtml).join('<br>')}</div>`;
 const message=paperOperation?.id===p.id?paperOperation.message:'';
 const lastPaper=(d.journals||[]).filter(j=>j.payload?.kind==='PAPER'&&j.payload.status==='COMPLETED').sort((a,b)=>String(b.recorded_at).localeCompare(String(a.recorded_at)))[0];
 const savedNotice=lastPaper?`Last paper rebalance saved: signal ${lastPaper.payload.signal_date}, applied ${lastPaper.payload.actual_trade_date}. `:'';
 const feedback=`<div class="card" role="status" aria-live="polite" id="paperOperationStatus">${escapeHtml(message||savedNotice+(paperReason||'Ready to apply the displayed paper target.'))}</div>`;
 h=h.replace('<div class="live-actions">'+(d.automatic_paper?'<button class="primary" onclick="applyPaperRebalance()"':'<button class="primary" onclick="openRebalanceReview()"'),feedback+'<div class="live-actions">'+(d.automatic_paper?'<button class="primary" onclick="applyPaperRebalance()"':'<button class="primary" onclick="openRebalanceReview()"'));
 $('liveContent').innerHTML=h;if(message)$('liveMessage').textContent=message;
 drawLiveChart(d.nav,st.continuity_starting_capital||p.capital,d.is_paper);loadLiveAudit();
}

function drawLiveChart(rows,capital,isPaper=false){
 const c=$('liveChart'),ctx=c.getContext('2d');c.width=Math.max(600,c.clientWidth)*2;c.height=560;
 const ledgerKey=isPaper?'paper_nav':'actual_nav';const all=[capital,...rows.flatMap(r=>[r[ledgerKey],r.model_nav]).filter(Number.isFinite)],min=Math.min(...all),max=Math.max(...all)+(Math.max(...all)===min?capital*.01:0),range=max-min;
 const left=120,right=c.width-30,top=35,bottom=490;
 ctx.fillStyle='#66717e';ctx.font='22px Segoe UI';ctx.strokeStyle='#dfe4e9';
 for(let i=0;i<5;i++){const y=top+i*(bottom-top)/4;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillText(money(max-i*range/4),2,y+7);}
 for(const [key,color] of [['model_nav','#1769d2'],[ledgerKey,'#0a7c49']]){ctx.strokeStyle=color;ctx.lineWidth=3;ctx.beginPath();[{[key]:capital},...rows].forEach((r,i)=>{const x=left+i*(right-left)/Math.max(rows.length,1),y=bottom-(r[key]-min)*(bottom-top)/range;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
 if(rows.length){ctx.fillText(rows[0].date,left,535);ctx.fillText(rows.at(-1).date,right-145,535);}
}

function openTrade(index){
 const t=Number.isInteger(index)?liveDetail.trades[index]:null;liveTradeCorrection=t?.id||null;liveTradeRequest=crypto.randomUUID();
 $('tradeTitle').textContent=t?'Correct Trade — original preserved':'Record Trade';$('tradeTicker').value=t?.ticker||'';$('tradeSide').value=t?.side||'BUY';$('tradeDate').value=t?.date||localToday();$('tradePrice').value=t?.price??'';$('tradeQuantity').value=t?.quantity??'';$('tradeFee').value=t?.fee??0;$('tradeNotes').value=t?.notes||'';$('tradeError').textContent='';$('tradeDialog').showModal();
}
async function saveLiveTrade(event){await formAction(event,'tradeError',async()=>{await api('/api/live/trade',{id:liveDetail.portfolio.id,ticker:$('tradeTicker').value,side:$('tradeSide').value,date:$('tradeDate').value,price:+$('tradePrice').value,quantity:+$('tradeQuantity').value,fee:+$('tradeFee').value,notes:$('tradeNotes').value,supersedes:liveTradeCorrection,request_id:liveTradeRequest});$('tradeDialog').close();await loadLiveDetail(true);});}
async function openRebalanceReview(){
 if(!await refreshRebalancePreview())return;
 rebalanceRequest=crypto.randomUUID();
 const wf=liveDetail.rebalance_workflow,rows=wf.recommendations.filter(r=>r.trade_side);
 $('rebalanceDialogInfo').textContent=`${wf.signal_date} signal → ${wf.execution_date} model execution · ${wf.strategy} · estimated cost RMB ${money(wf.estimated_model_cost)}`;
 $('rebalanceTradeRows').innerHTML='<table><caption>Suggested quantities use the displayed reference price; enter only confirmed actual fills.</caption><tr><th>Use</th><th>Action</th><th>Ticker / Name</th><th>Suggested Value (RMB)</th><th>Suggested Quantity</th><th>Actual Date</th><th>Actual Price (RMB)</th><th>Actual Quantity</th><th>Actual Fee (RMB)</th><th>Notes</th></tr>'+rows.map((r,i)=>{const side=r.trade_side,price=Number(r.reference_price)||0,qty=Math.abs(Number(r.suggested_quantity_change)||0);return `<tr data-rebalance-row="${i}" data-ticker="${escapeHtml(r.ticker)}" data-side="${side}"><td><input class="rb-use" type="checkbox" checked></td><td>${escapeHtml(r.action)}</td><td>${escapeHtml(r.ticker)}<br>${escapeHtml(r.name)}</td><td>${money(r.suggested_trade_value)}</td><td>${money(r.suggested_quantity_change)}</td><td><input class="rb-date" type="date" value="${escapeHtml(liveDetail.asof||localToday())}"></td><td><input class="rb-price" type="number" min="0" step="any" value="${price||''}"></td><td><input class="rb-qty" type="number" min="0" step="any" value="${qty?qty.toFixed(4):''}"></td><td><input class="rb-fee" type="number" min="0" step="any" value="0"></td><td><input class="rb-note" maxlength="200"></td></tr>`}).join('')+'</table>';
 $('rebalanceCompleted').checked=false;$('rebalanceNotes').value='';$('rebalanceError').textContent='';$('rebalanceDialog').showModal();
}
async function saveActualRebalance(event){await formAction(event,'rebalanceError',async()=>{
 const trades=[...$('rebalanceTradeRows').querySelectorAll('[data-rebalance-row]')].filter(row=>row.querySelector('.rb-use').checked).map(row=>({ticker:row.dataset.ticker,side:row.dataset.side,date:row.querySelector('.rb-date').value,price:+row.querySelector('.rb-price').value,quantity:+row.querySelector('.rb-qty').value,fee:+row.querySelector('.rb-fee').value,notes:row.querySelector('.rb-note').value}));
 await api('/api/live/actual-rebalance',{id:liveDetail.portfolio.id,signal_id:liveDetail.rebalance_workflow.signal_id,request_id:rebalanceRequest,trades,...rebalanceCapital,mark_completed:$('rebalanceCompleted').checked,notes:$('rebalanceNotes').value});$('rebalanceDialog').close();rebalanceCapital={capital_adjustment:'NONE',capital_amount:0};await loadLiveDetail(true);
 });}
async function refreshRebalancePreview(){try{const adjustment={capital_adjustment:$('capitalAdjustment').value,capital_amount:+$('capitalAmount').value};const p=await api('/api/live/rebalance-preview',{id:liveDetail.portfolio.id,...adjustment});rebalanceCapital=adjustment;Object.assign(liveDetail.rebalance_workflow,p,{estimated_model_cost:p.estimated_trading_cost,current_portfolio_value:p.current_nav});renderLive();return true;}catch(e){$('liveMessage').textContent=e.message;return false;}}
async function applyPaperRebalance(){
 if(paperApplyPending||!liveDetail)return;
 const id=liveDetail.portfolio.id,signal_id=liveDetail.rebalance_workflow.signal_id;
 const adjustment={capital_adjustment:$('capitalAdjustment').value,capital_amount:Number($('capitalAmount').value)};
 paperApplyPending=true;
 paperFeedback('Applying paper rebalance: validating the target and saving the paper ledger…');
 renderLive();
 const started=Date.now();
 const timer=setInterval(()=>{if(paperOperation?.id===id)paperFeedback(`Applying paper rebalance: waiting for server confirmation · ${Math.floor((Date.now()-started)/1000)} seconds. Do not submit again.`);},1000);
 let saved=false;
 try{
  // The server validates the target and computes the proposal itself. Repeating
  // the expensive preview here adds a silent delay and is not a safety check.
  const result=await api('/api/live/paper-rebalance',{id,signal_id,...adjustment});
  saved=true;clearInterval(timer);
  paperFeedback(`Paper rebalance saved (${result.id}). Refreshing portfolio…`);
  const detail=await api('/api/live/detail',{id,refresh:true});
  if($('liveSelector').value===id){liveDetail=detail;rebalanceCapital={capital_adjustment:'NONE',capital_amount:0};}
  paperFeedback(`Paper rebalance completed and portfolio refreshed. Record: ${result.id}`);
 }catch(e){paperFeedback(saved?`Paper rebalance was saved, but the display refresh failed: ${e.message}. Reload portfolios; do not apply again.`:`Paper rebalance could not be confirmed: ${e.message}. Reload the portfolio and check its journal before retrying.`);}
 finally{clearInterval(timer);paperApplyPending=false;renderLive();}
}
async function skipCurrentRebalance(){if(!confirm('Mark this model rebalance skipped? Current holdings will remain unchanged.'))return;try{await api('/api/live/skip-rebalance',{id:liveDetail.portfolio.id,signal_id:liveDetail.rebalance_workflow.signal_id});await loadLiveDetail(true);}catch(e){$('liveMessage').textContent=e.message;}}
async function changeLiveStatus(){try{await api('/api/live/status',{id:liveDetail.portfolio.id,status:$('liveStatus').value});await loadLivePortfolios(liveDetail.portfolio.id);}catch(e){$('liveMessage').textContent=e.message;}}
function openJournal(){$('journalSignal').innerHTML=liveDetail.signals.map(s=>`<option value="${s.id}">${s.signal_date} · ${s.payload.backfilled?'reconstructed':'tracking'}</option>`).join('');$('journalSignal').value=liveDetail.latest_signal_id;$('journalDate').value=localToday();$('journalNotes').value='';$('journalError').textContent='';$('journalDialog').showModal();}
async function saveLiveJournal(event){await formAction(event,'journalError',async()=>{await api('/api/live/journal',{id:liveDetail.portfolio.id,date:$('journalDate').value,signal_id:$('journalSignal').value,notes:$('journalNotes').value});$('journalDialog').close();await loadLiveDetail();});}
function loadLiveAudit(){clearPagedTables('events');if(!liveDetail){$('liveAudit').textContent='Select a portfolio in Live Portfolio first.';return;}$('liveAudit').innerHTML=pagedTable('events-records',liveDetail.events,page=>`<div class="card"><h3>${escapeHtml(liveDetail.portfolio.name)} — Permanent Events</h3><div class="table-scroll"><table><tr><th>Timestamp (UTC)</th><th>Event</th><th>Detail</th></tr>${page.map(e=>`<tr><td>${escapeHtml(e.recorded_at)}</td><td>${escapeHtml(e.event_type)}</td><td><details><summary>View recorded payload</summary><p>${escapeHtml(JSON.stringify(e.payload))}</p></details></td></tr>`).join('')}</table></div></div>`);}
async function exportLive(){if(!liveDetail){$('liveExports').textContent='Select a live portfolio first.';return;}try{const r=await api('/api/live/export',{id:liveDetail.portfolio.id});$('liveExports').innerHTML=Object.entries(r.files).map(([name,path])=>`<p><a href="/file?path=${encodeURIComponent(path)}" download="${escapeHtml(name)}">${escapeHtml(name)}</a></p>`).join('');activatePage('audit');}catch(e){$('liveExports').textContent=e.message;}}
async function archiveLive(){try{await api('/api/live/archive',{id:liveDetail.portfolio.id});$('liveView').value='archived';await loadLivePortfolios(liveDetail.portfolio.id);}catch(e){$('liveMessage').textContent=e.message;}}
async function restoreLive(){try{const id=liveDetail.portfolio.id;await api('/api/live/restore',{id});$('liveView').value='active';await loadLivePortfolios(id);}catch(e){$('liveMessage').textContent=e.message;}}
function openDeletePortfolio(){const p=liveDetail.portfolio,s=liveDetail.stats;$('deletePortfolioSummary').innerHTML=`<table><caption>Portfolio selected for permanent deletion</caption><tr><th>Portfolio Name</th><td>${escapeHtml(p.name)}</td></tr><tr><th>Status</th><td>${escapeHtml(p.archived?'ARCHIVED':p.status)}</td></tr><tr><th>Start Date</th><td>${escapeHtml(p.start_date)}</td></tr><tr><th>Current NAV (RMB)</th><td>${money(s.current_nav)}</td></tr><tr><th>Trades</th><td>${liveDetail.trades.length}</td></tr><tr><th>Rebalances</th><td>${liveDetail.journals.length}</td></tr></table>`;$('deletePortfolioConfirmation').value='';$('deletePortfolioError').textContent='';$('deletePortfolioDialog').showModal();}
async function confirmDeletePortfolio(event){await formAction(event,'deletePortfolioError',async()=>{await api('/api/live/delete',{id:liveDetail.portfolio.id,confirmation:$('deletePortfolioConfirmation').value});$('deletePortfolioDialog').close();liveDetail=null;await loadLivePortfolios();});}
function openPromotion(){$('promoteName').value=liveDetail.portfolio.name+' — Active';$('promoteDate').value=localToday();$('promoteOpeningState').textContent=`Opening NAV RMB ${money(liveDetail.stats.current_nav)} · Cash RMB ${money(liveDetail.stats.cash)} · ${liveDetail.positions.filter(r=>r.quantity>0).length} carried holdings.`;$('promoteError').textContent='';$('promoteDialog').showModal();}
async function continueAsActive(event){await formAction(event,'promoteError',async()=>{const result=await api('/api/live/promote',{id:liveDetail.portfolio.id,name:$('promoteName').value,activation_date:$('promoteDate').value});$('promoteDialog').close();$('liveView').value='active';await loadLivePortfolios(result.id);});}

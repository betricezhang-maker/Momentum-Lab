/* V4 view layer. Backtest math stays on the server. */
let v4Strategies=[];
let pendingLiveSource=null;
const v4Metrics=[['gross_return','Gross Return'],['net_return','Net Return'],['trading_cost_drag','Cost Drag (return pp)'],['gross_cagr','Gross CAGR'],['net_cagr','Net CAGR'],['gross_max_drawdown','Gross Max DD'],['net_max_drawdown','Net Max DD'],['average_turnover','Average Turnover / Rebalance'],['median_turnover','Median Turnover'],['total_turnover','Cumulative Turnover'],['annualized_turnover','Annualized Turnover'],['average_holdings_retained','Avg Holdings Retained'],['average_holdings_replaced','Avg Holdings Replaced'],['maximum_rebalance_turnover','Maximum Turnover'],['holdings_retention_rate','Holdings Retention Rate']];
document.getElementById('strategygrid').append(document.getElementById('strategyGridCard'));
for(const [value,label] of v4Metrics){const option=document.createElement('option');option.value=value;option.textContent=label;$('gridMetric').append(option);}

function enhanceV4Grid(j,strategies){
 v4Strategies=strategies;
 const table=$('gridSummary').querySelector('table');if(!table)return;
 const metrics=[['annualized_return','CAGR (gross)'],['annualized_volatility','Annual Volatility (gross)'],...v4Metrics.filter(([k])=>!['gross_return','gross_cagr','gross_max_drawdown'].includes(k))];
 for(const label of [...metrics.map(x=>x[1]),'Rebalance Audit','Live Tracking']){const th=document.createElement('th');th.textContent=label;table.rows[0].append(th);}
 strategies.forEach((s,i)=>{
  const tr=table.rows[i+1];const full=s.full&&!s.full.error?s.full:null;
  for(const [key] of metrics){const td=document.createElement('td');td.textContent=gridMetricText(key,full?.[key]);td.title=key.includes('turnover')?'Recurring rebalances exclude initial funding; annualization uses 252 observations.':'Full Period result; — if Full Period was not run.';tr.append(td);}
  let td=document.createElement('td');let button=document.createElement('button');button.className='secondary';button.textContent='History';button.onclick=()=>showGridHistory(i);td.append(button);tr.append(td);
  td=document.createElement('td');button=document.createElement('button');button.className='primary';button.textContent='Add to Live Portfolio';button.disabled=!s.rr.some(r=>r&&!r.error)&&!full;button.onclick=()=>promoteGridStrategy(i);td.append(button);tr.append(td);
 });
}

async function showGridHistory(index){
 const s=v4Strategies[index],j=strategyGridData;
 const row=j.rows.find(r=>r.period===$('gridPeriod').value&&r.lookback===s.lb&&r.rebalance_days===s.rb&&r.selection===s.sel);
 $('historyDialog').showModal();$('historyContent').textContent='Loading history…';
 try{
  if(!row||row.error||!row.history_file)throw new Error(row?.error||'No V4 history exists for this period. Run the grid again.');
  const response=await fetch('/file?path='+encodeURIComponent(row.history_file));if(!response.ok)throw new Error('History could not be read.');
  const records=await response.json();
  const historyCsv=row.history_file.replace(/\.json$/i,'.csv');
  const exports=`<p><a class="secondary" download href="${escapeHtml(fileUrl(historyCsv))}">Download all ${records.length} rebalances (CSV / Excel)</a> · <a download href="${escapeHtml(fileUrl(row.history_file))}">Download full audit (JSON)</a></p><p class="small">Exports the selected strategy and period across all history pages, including initial funding. Weights and turnover are decimal fractions: 0.20 = 20%. CSV includes tickers, old/target weights, turnover, costs and NAV; JSON includes saved provenance.</p>`;
  $('historyContent').innerHTML=`<p>${escapeHtml(j.universe_label)} · MOM${s.lb} · ${escapeHtml(s.sel)} · ${s.rb}D · ${escapeHtml(row.period)} · ${j.trading_cost_bps} bps per one-way turnover</p>`+exports+rebalanceHistoryTable(records);
 }catch(e){$('historyContent').textContent=e.message;}
}

function rebalanceHistoryTable(records,key='history-dialog'){
 return pagedTable(key,records,page=>rebalanceHistoryPage(page));
}
function rebalanceHistoryPage(records){
 return '<div class="table-scroll"><table><caption>Pre-trade weights → target weights; cost includes initial funding, recurring averages exclude it.</caption><tr><th>Signal → Execution</th><th>Retained</th><th>Sold</th><th>Bought</th><th>Turnover</th><th>Cost / initial NAV</th><th>Details</th></tr>'+records.map(r=>`<tr><td>${escapeHtml(r.signal_date)} → ${escapeHtml(r.entry_date)}${r.initial_funding?' (initial funding)':''}</td><td>${r.retained}</td><td>${r.sold}</td><td>${r.bought}</td><td>${pct(r.one_way_turnover)}</td><td>${pct(r.cost)}</td><td><details><summary>KEEP / SELL / BUY / weights</summary><p>KEEP: ${escapeHtml(r.keep||'—')}<br>SELL: ${escapeHtml(r.sell||'—')}<br>BUY: ${escapeHtml(r.buy||'—')}</p><p>Old: ${escapeHtml(r.old_weights)}<br>Target: ${escapeHtml(r.target_weights)}</p></details></td></tr>`).join('')+'</table></div>';
}

function promoteGridStrategy(index){
 const s=v4Strategies[index],j=strategyGridData;
 pendingLiveSource={run_id:j.run_id,lookback:s.lb,selection:s.sel,rebalance_days:s.rb};
 $('createStrategyIdentity').textContent=`${j.universe_label} · MOM${s.lb} / ${s.sel} / ${s.rb}D · ${j.trading_cost_bps} bps · Source ${j.run_id}`;
 $('liveName').value=`${j.universe_label} MOM${s.lb} ${s.sel} ${s.rb}D Pilot`;
 $('liveStartMode').value='today';$('liveStart').value=localToday();$('liveEnd').value=localToday();$('liveCost').value=j.trading_cost_bps;$('liveCapital').value='';toggleHistoricalInputs();$('createLiveError').textContent='';$('createLiveDialog').showModal();
}

function renderV4Research(j){
 let box=$('v4Research');if(!box){box=document.createElement('div');box.id='v4Research';box.className='card';$('researchOut').prepend(box);}
 box.innerHTML='<h3>Gross vs Net — Turnover and Costs</h3><div class="table-scroll"><table><caption>Gross = V3 zero-cost reference; net includes funding and recurring turnover costs. Cost drag is the gross/net cumulative-return difference in percentage points.</caption><tr>'+v4Metrics.map(x=>`<th>${escapeHtml(x[1])}</th>`).join('')+'</tr><tr>'+v4Metrics.map(([key])=>`<td>${gridMetricText(key,j.stats[key])}</td>`).join('')+'</tr></table></div><details><summary>Rebalance History — KEEP / SELL / BUY</summary>'+rebalanceHistoryTable(j.rebalance_history||[],'history-research')+'</details>';
}

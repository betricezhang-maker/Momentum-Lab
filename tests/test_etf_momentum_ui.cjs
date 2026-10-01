const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('ui/index.html','utf8');
const code=html.slice(html.indexOf('   const em=j.etf_momentum_monitor;'),html.indexOf('   labelResearchResults(j);'));
const nodes={};const $=id=>nodes[id]??={style:{},innerHTML:'',textContent:'',src:''};
function render(em){vm.runInNewContext(code,{$,j:{etf_momentum_monitor:em},escapeHtml:String,fileUrl:x=>'/file?path='+x,pct:x=>x==null?'Unavailable':(x*100)+'%',pagedTable:(key,rows,render)=>render(rows)});}
render({status:'available',note:'Coverage warning',chart:'test.png',csv:'test.csv',observations:10,unavailable_participation:2,unavailable_top10:0});
assert.equal($('etfMomentumPanel').style.display,'block');assert.match($('etfMomentumChart').src,/test.png/);
assert.match($('etfMomentumEvidence').innerHTML,/Download indicator data/);
assert(!code.includes('recorded signal dates'));assert(!code.includes("pagedTable('etf-monitor'"));assert(!code.includes('renderETFConditional'));
render(null);assert.equal($('etfMomentumPanel').style.display,'none');assert.equal($('etfMomentumEvidence').innerHTML,'');
render({status:'unavailable',note:'Missing evidence'});assert.equal($('etfMomentumChart').style.display,'none');assert.equal($('etfMomentumInfo').textContent,'Missing evidence');
assert(!html.includes('marketRegime'));assert(html.includes('etfMomentumPanel'));
assert(html.includes('id="etfParticipationCoverage" type="number" min="1" max="100" step="1" value="80"'));
assert(html.includes("etf_participation_min_coverage:Number($('etfParticipationCoverage').value)/100"));
const toggle=html.slice(html.indexOf('function updateETFVisualization(){'),html.indexOf('async function runResearch()'));
const ctx=vm.createContext({$,fileUrl:x=>'/file?path='+x});vm.runInContext(toggle,ctx);
$('etfMomentumChart').etfVariants={'00':'base.png','10':'median.png','01':'colors.png','11':'both.png'};
for(const [median,colors,file] of [[false,false,'base.png'],[true,false,'median.png'],[false,true,'colors.png'],[true,true,'both.png']]){
 $('etfMedianOverlay').checked=median;$('etfReturnColors').checked=colors;vm.runInContext('updateETFVisualization()',ctx);assert.equal($('etfMomentumChart').src,'/file?path='+file);
}
console.log('PASS: automatic ETF rendering, coverage/download link, non-ETF hiding, stale result removal and failure display.');
assert(!html.includes('simThreshold'));assert(!html.includes('etfMomentumScatter'));assert(!html.includes('renderCashWarning'));
console.log('PASS: simulator and extra diagnostic chart sections removed.');

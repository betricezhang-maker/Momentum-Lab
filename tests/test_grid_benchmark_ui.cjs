// Regression test: the cross-year grid table is assembled by TWO scripts.
// renderStrategyGrid (ui/index.html) appends a benchmark reference row, then
// enhanceV4Grid (ui/strategy-grid.js) appends the V4 metric columns to the
// header and to every strategy row. If enhanceV4Grid does not also extend the
// benchmark row, that row is left short and the benchmark shows no values under
// the V4 columns. test_ui.cjs never caught this because it does not load
// strategy-grid.js. This harness loads both and feeds a real run_metadata.json.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const uiDir = path.join(__dirname, '..', 'ui');
const html = fs.readFileSync(path.join(uiDir, 'index.html'), 'utf8');
const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const gridJs = fs.readFileSync(path.join(uiDir, 'strategy-grid.js'), 'utf8');

// Pull whole function definitions out of the minified inline script by brace
// matching (braces inside regex/templ literals are balanced, so depth works).
function extractFn(src, name) {
  const i = src.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('function not found: ' + name);
  let k = src.indexOf('{', i), depth = 0;
  for (; k < src.length; k++) {
    const c = src[k];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) { k++; break; } }
  }
  return src.slice(i, k);
}
const fns = ['escapeHtml', 'returnStyle', 'drawdownStyle', 'consistencyPill',
  'strategyQuality', 'gridMetricText', 'gridCellStyle', 'renderStrategyGrid']
  .map(n => extractFn(inline, n)).join('\n');

// --- minimal DOM shim -------------------------------------------------------
function makeEl(id) {
  return {
    id, value: '', className: '', textContent: '', title: '', disabled: false,
    selectedOptions: [{ textContent: 'Total Return' }],
    children: [], append(c) { this.children.push(c); },
    set onclick(f) { this._onclick = f; }, get onclick() { return this._onclick; },
    _html: '', _table: null,
    set innerHTML(v) { this._html = v; if (id === 'gridSummary') this._table = parseTable(v); },
    get innerHTML() { return this._html; },
    querySelector(sel) {
      if (id === 'gridSummary' && sel === 'table') return this._table;
      return null;
    },
  };
}
function parseTable(h) {
  const t = h.slice(h.indexOf('<table'), h.indexOf('</table>') + 8);
  const rows = [];
  const re = /<tr([^>]*)>([\s\S]*?)<\/tr>/g; let m;
  while ((m = re.exec(t))) {
    const attrs = m[1], body = m[2];
    const cls = (attrs.match(/class="([^"]*)"/) || [, ''])[1];
    const cells = (body.match(/<t[dh]/g) || []).length;
    rows.push({ className: cls, parsedCells: cells, appended: [], append(c) { this.appended.push(c); } });
  }
  return {
    rows,
    querySelector(sel) {
      if (sel === 'tr.benchrow') return rows.find(r => r.className.includes('benchrow')) || null;
      return null;
    },
  };
}
const els = {};
const document = {
  getElementById: id => (els[id] || (els[id] = makeEl(id))),
  createElement: tag => ({ tagName: tag, textContent: '', title: '', className: '', disabled: false, children: [], append(c) { this.children.push(c); }, set onclick(f) { this._onclick = f; }, get onclick() { return this._onclick; } }),
};

const context = vm.createContext({ document, console, Number, Math, JSON, Array, Object, String, isNaN, parseFloat });
vm.runInContext('const $=id=>document.getElementById(id);\n' + fns, context);
// strategy-grid.js runs its top-level wiring on load (needs $ + document).
vm.runInContext(gridJs, context);

// --- load a real grid run ---------------------------------------------------
const resultsRoot = path.join(__dirname, '..', 'results', 'backtests');
const runs = fs.readdirSync(resultsRoot).filter(d => /strategy_grid/.test(d)).sort();
let data = null, runId = null;
for (const r of runs.reverse()) {
  const p = path.join(resultsRoot, r, 'run_metadata.json');
  if (!fs.existsSync(p)) continue;
  const d = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (Array.isArray(d.rows) && d.rows.some(x => x.benchmark_return != null)) { data = d; runId = r; break; }
}
if (!data) { console.log('SKIP: no saved grid run with benchmark data found'); process.exit(0); }

const years = data.periods.filter(p => /^\d{4}$/.test(p));
els.gridPeriod = makeEl('gridPeriod'); els.gridPeriod.value = years[0];
els.gridMetric = makeEl('gridMetric'); els.gridMetric.value = 'total_return';
els.gridSelection = makeEl('gridSelection'); els.gridSelection.value = data.selection_rules[0];
els.gridMeta = makeEl('gridMeta'); els.gridSummary = makeEl('gridSummary'); els.gridMatrix = makeEl('gridMatrix');

const original = JSON.stringify(data);
vm.runInContext('strategyGridData=theData; renderStrategyGrid();', Object.assign(context, { theData: data }));
assert.equal(JSON.stringify(data), original, 'rendering must not mutate API data');

const table = els.gridSummary._table;
assert.ok(table, 'gridSummary table parsed');
const benchRow = table.querySelector('tr.benchrow');
assert.ok(benchRow, 'benchmark row must be present when benchmark data exists');

const cellCount = r => r.parsedCells + r.appended.length;
const header = table.rows[0];
const strategyRows = table.rows.slice(1, table.rows.indexOf(benchRow));
const counts = table.rows.map(cellCount);
assert.ok(strategyRows.length >= 1, 'has strategy rows');
// Every row — header, strategies, benchmark — must have identical column count.
for (const r of table.rows) {
  assert.equal(cellCount(r), cellCount(header),
    `row cell count ${cellCount(r)} != header ${cellCount(header)} (class="${r.className}")`);
}
// enhanceV4Grid must extend the benchmark row too (14 metrics + audit + live).
assert.equal(benchRow.appended.length, header.appended.length,
  'benchmark row must receive the same appended V4 columns as the header');
assert.equal(benchRow.appended.length, 16, 'expected 16 appended V4 columns');

// The benchmark V4 cells must carry real values, not all em-dashes.
const benchTexts = benchRow.appended.map(td => td.textContent);
const num = v => v != null && Number.isFinite(Number(v));
const bf = data.rows.find(r => r.period === 'Full Period' && num(r.benchmark_return));
assert.ok(bf, 'Full Period benchmark row exists in run data');
// CAGR (gross) is the first appended metric column.
const expCagr = (bf.benchmark_annualized_return * 100).toFixed(2) + '%';
assert.equal(benchTexts[0], expCagr, 'benchmark CAGR (gross) column populated');
// Ann Volatility (gross) is second.
assert.equal(benchTexts[1], (bf.benchmark_annualized_volatility * 100).toFixed(2) + '%', 'benchmark ann vol column populated');
assert.ok(benchTexts.filter(t => t !== '—').length >= 4, 'benchmark row shows multiple real V4 values');

// Matrix benchmark row is untouched by enhanceV4Grid and must still be present.
assert.match(els.gridMatrix.innerHTML, /buy-and-hold/, 'matrix benchmark row present');

console.log('PASS: enhanceV4Grid extends the benchmark row — all gridSummary rows have '
  + cellCount(header) + ' columns; benchmark V4 cells: '
  + benchTexts.slice(0, 6).join(' | ') + ' … (run ' + runId + ')');

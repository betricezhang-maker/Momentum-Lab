const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const {chromium}=require(process.env.MOMENTUM_PLAYWRIGHT_MODULE||'playwright');
const [base,root,python]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.MOMENTUM_BROWSER_CHANNEL?{channel:process.env.MOMENTUM_BROWSER_CHANNEL}:{})});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/test-review');await page.getByRole('button',{name:'Run Integrity Check',exact:true}).click();
  await page.getByRole('button',{name:'Export issues for review (Excel)',exact:true}).waitFor();
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Export issues for review (Excel)',exact:true}).click();
  const download=await pending;const workbook=path.join(root,'review.xlsx');await download.saveAs(workbook);
  execFileSync(python,['-c',"from openpyxl import load_workbook; import sys; p=sys.argv[1]; w=load_workbook(p); s=w['Review']; rows=[r for r in s.iter_rows(min_row=2) if r[5].value=='A' and 'raw_price' in r[8].value]; r=rows[0]; r[16].value='confirm'; r[17].value='已核对，accepted gap'; r[18].value='local test evidence'; w.save(p)",workbook]);
  await page.locator('input[type=file]').setInputFiles(workbook);await page.getByRole('button',{name:'Apply reviewed decisions',exact:true}).waitFor();
  assert.match(await page.locator('#reviewPreview').innerText(),/已核对/);
  const ackPath=path.join(root,'completeness_acknowledgments.json');assert.equal(fs.existsSync(ackPath),false,'Import preview must not apply');
  await page.getByRole('button',{name:'Apply reviewed decisions',exact:true}).click();
  await page.getByText('Bulk operation results',{exact:true}).waitFor({timeout:30000});
  const saved=JSON.parse(fs.readFileSync(ackPath,'utf8'));assert.equal(saved.length,2);assert.equal(saved[1].inherited_from,saved[0].confirmation_id);assert.equal(saved[0].explanation,saved[1].explanation);
  assert.equal(await page.getByRole('button',{name:'Reopen',exact:true}).count(),2);
  await page.reload();await page.getByRole('button',{name:'Run Integrity Check',exact:true}).click();await page.getByRole('button',{name:'Reopen',exact:true}).first().waitFor();
  await page.locator('#reviewStatus').selectOption('CONFIRMED');assert.equal(await page.locator('tr[data-issue-id]:visible').count(),2);
  await page.locator('input[type=file]').setInputFiles(workbook);await page.getByRole('button',{name:'Apply reviewed decisions',exact:true}).waitFor();
  assert.match(await page.locator('#reviewPreview').innerText(),/ALREADY_APPLIED/);
  await page.getByRole('button',{name:'Apply reviewed decisions',exact:true}).click();await page.getByText('Bulk operation results',{exact:true}).waitFor();assert.equal(JSON.parse(fs.readFileSync(ackPath,'utf8')).length,2);
  await page.getByRole('button',{name:'Select visible findings',exact:true}).click();await page.getByRole('button',{name:'Repair selected',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('reviewPreview')?.textContent.includes('Still missing'),{timeout:30000});
  assert.match(await page.locator('#reviewPreview').innerText(),/false/);
  assert.deepEqual(errors,[]);console.log('PASS: browser export/download/edit/upload/preview/apply, Chinese reason, inherited persistence, reload, duplicate import, filters and empty repair.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

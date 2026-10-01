/* Read-only smoke check of the already running source launcher. */
const assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(process.env.MOMENTUM_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.MOMENTUM_BROWSER_CHANNEL?{channel:process.env.MOMENTUM_BROWSER_CHANNEL}:{})});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error(e.stack)});
  page.on('requestfailed',request=>console.error('Request failed:',request.url(),request.failure()?.errorText));
  await page.goto('http://127.0.0.1:8765/');await page.locator('[data-page="manager"]').first().click();
  await page.locator('#wsCheck').waitFor();
  assert.equal(await page.locator('#integrityDashboard').count(),1);
  assert.equal(await page.locator('#wsCheck').count(),1);
  assert.equal(await page.locator('#wsExportPage').count(),1);
  assert.equal(await page.locator('#wsUploadButton').count(),1);
  await page.screenshot({path:path.resolve('diagnostics/integrity_live_source_smoke.png'),fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS: running portable source serves one integrity workspace and current controls. No production integrity action was executed.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');
const {browserGuard} = require('../browser/guard.cjs');
const {workdayAuthClickTarget} = require('../browser/workday-auth.cjs');

// A generic Submit overlay is permitted only as the exact active login target.
async function checkSignIn(page, native) {
  await native.evaluate(e=>{e.textContent='Sign In';e.setAttribute('data-automation-id','signInSubmitButton')});
  await page.$eval('[role=button]',e=>e.setAttribute('aria-label','Submit'));
  const target=await workdayAuthClickTarget(native,{text:'Sign In'});
  assert.equal(await target.evaluate(e=>e.tagName),'DIV');
  await page.evaluate(browserGuard);
  await page.evaluate(()=>{window.clicked=false});
  await target.click();
  assert.equal(await page.evaluate(()=>window.clicked),false);
  await target.evaluate(e=>{globalThis.__applyDesk.authTarget=e;globalThis.__applyDesk.authUntil=Date.now()+1000});
  await target.click();
  assert.equal(await page.evaluate(()=>window.clicked),true);
  await page.evaluate(()=>{window.clicked=false;globalThis.__applyDesk.authUntil=0});
  await target.click();
  assert.equal(await page.evaluate(()=>window.clicked),false);
}

// The visible overlay must receive a real click without forcing the covered native button.
(async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-workday-click-'));
  const session=await start({dataRoot:root,headless:true,allowLocal:true});
  try {
    const page=await session.context.newPage();
    await page.setContent(`<form><div style="position:relative;width:200px;height:40px">
      <div role="button" aria-label="Create Account" data-automation-id="click_filter"
        style="position:absolute;inset:0;z-index:1" onclick="window.clicked=true"></div>
      <button type="submit" aria-hidden="true" style="width:100%;height:100%">Create Account</button>
      </div></form>`);
    const native=await page.$('button');
    const target=await workdayAuthClickTarget(native,{text:'Create Account'});
    await target.click({timeout:1000});
    assert.equal(await page.evaluate(()=>window.clicked),true);
    await page.$eval('[role=button]',e=>e.setAttribute('aria-label','Different action'));
    const rejected=await workdayAuthClickTarget(native,{text:'Create Account'});
    assert.equal(await rejected.evaluate(e=>e.tagName),'BUTTON');
    await checkSignIn(page,native);
    console.log('Workday visible authentication target and mismatched overlay rejection verified.');
  }finally{await dispatch(session,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

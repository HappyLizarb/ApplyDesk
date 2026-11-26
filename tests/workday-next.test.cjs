const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {installOwnedPageGuards}=require('../browser/guard.cjs');
const {inspect}=require('../browser/inspect.cjs');
const {advance,fill}=require('../browser/actions.cjs');
const {pauseWorkspace}=require('../browser/control.cjs');
const {settleResearchPage}=require('../browser/page-settling.cjs');
const appUrl='https://fixture.wd5.myworkdayjobs.com/en-US/Site/job/US/Engineer_JR1/apply/applyManually';
const termsPath='/wday/calypso/cxs/jobapplication/fixture/Site/termsdata';

// Workday disables its footer before asynchronous Next work, while the old step can remain visible.
function html(options) {
  return `<!doctype html><title>Fixture company Production Editor</title><main data-automation-id="applyFlowPage">
    <h1>Fixture company Production Editor</h1><nav>${'Application navigation. '.repeat(12)}</nav>
    <section data-automation-id="applyFlowMyInfoPage"><label>First Name<input name="first" value="Example"></label><div role="alert"></div><div role="alert">Information saves automatically.</div></section>
    <footer data-automation-id="pageFooter"><button type="button" data-automation-id="pageFooterNextButton" ${options.initialDisabled?'disabled':''}>Save and Continue</button></footer></main>
    <script>const options=${JSON.stringify(options)};window.fixtureTrace={};document.querySelector('button').onclick=()=>{
      window.fixtureTrace.clicked=performance.now();const button=document.querySelector('button');
      if(options.aria)button.setAttribute('aria-disabled','true');else button.disabled=true;
      if(options.stalled)return;
      setTimeout(()=>{
        if(options.validation){document.querySelector('input').setAttribute('aria-invalid','true');
          document.querySelector('section').insertAdjacentHTML('beforeend','<p role="alert">Please check the supplied name.</p>');return;}
        if(options.error){document.querySelector('section').textContent='Something went wrong. Please refresh the page and then try again.';return;}
        window.fixtureTrace.readStarted=performance.now();fetch('${termsPath}').then(r=>r.json()).then(()=>{
          document.querySelector('section').outerHTML='<section data-automation-id="applyFlowLoadingPage">Loading next step</section>';
          setTimeout(()=>{document.querySelector('section').outerHTML='<section data-automation-id="applyFlowMyExpPage"><label>School<input name="school"></label></section>';
            button.removeAttribute('aria-disabled');button.disabled=!!options.nextDisabled;window.fixtureTrace.rendered=performance.now();},200);
        });
      },options.startDelay||0);
    };</script>`;
}

// Page routing supplies synthetic responses; no personal profile or employer connection is used.
async function fixture(browser,options) {
  const context=await browser.newContext(),page=await context.newPage();
  const state={normalNetworking:true,manual:false,paused:false,generation:0,controller:new AbortController(),blocked:[],allowLocal:true};
  await installOwnedPageGuards(page,state);
  await page.route('**/*',async route=>{
    if(new URL(route.request().url()).pathname===termsPath){
      await new Promise(resolve=>setTimeout(resolve,options.responseDelay||100));
      await route.fulfill({contentType:'application/json',body:'{}'});
    }else await route.fulfill({contentType:'text/html',body:html(options)});
  });
  await page.goto(appUrl);
  return {context,page,state};
}

// Use the public advance path and an exact inspected audit, including the real native click.
async function next(f) {
  const snapshot=await inspect(f.page,f.state);
  const button=snapshot.buttons.find(item=>item.text==='Save and Continue');
  const audit={passed:true,snapshot_id:snapshot.snapshot_id,field_ids:snapshot.fields.map(field=>field.field_id)};
  return advance(f.page,f.state,{snapshot_id:snapshot.snapshot_id,field_id:button.field_id,audit});
}

// Pending reads, delayed initiation, aria-busy controls and a newly disabled form all settle correctly.
async function readableSteps(browser) {
  const cases=[{responseDelay:1800},{startDelay:2000,responseDelay:400},
    {startDelay:1700,aria:true},{startDelay:1700,nextDisabled:true}];
  for(const options of cases){
    const f=await fixture(browser,options);
    try{
      const result=await next(f);
      assert.deepEqual(result.fields.map(field=>field.name),['school']);
      assert.equal(result.buttons.find(button=>button.text==='Save and Continue').disabled,!!options.nextDisabled);
    }finally{await f.context.close();}
  }
}

// Validation feedback and a visible site error must return for inspection instead of timing out.
async function completedOldStep(browser) {
  for(const options of [{startDelay:1700,validation:true},{startDelay:1700,error:true}]){
    const f=await fixture(browser,options);
    try{
      const result=await next(f);
      if(options.validation)assert(result.text.includes('Please check the supplied name.'));
      else assert(result.warnings.some(warning=>warning.includes('Employer application page error')));
    }finally{await f.context.close();}
  }
}

// An initially disabled footer does not prevent reading and filling the initial form.
async function initialForm(browser) {
  const f=await fixture(browser,{initialDisabled:true});
  try{
    await settleResearchPage(f.page);
    const before=await inspect(f.page,f.state),first=before.fields.find(field=>field.name==='first');
    const result=await fill(f.page,f.state,{snapshot_id:before.snapshot_id,answers:[{field_id:first.field_id,value:'Confirmed'}]});
    assert.equal(result.readbacks[0].value,'Confirmed');
  }finally{await f.context.close();}
}

// Stop interrupts the controller's wait promptly while the employer's already-started read finishes.
async function cancellation(browser) {
  const f=await fixture(browser,{responseDelay:1800});
  try{
    const request=f.page.waitForRequest(request=>new URL(request.url()).pathname===termsPath);
    const pending=next(f),rejected=assert.rejects(pending,/paused/i);
    await request;const stopped=Date.now();
    await pauseWorkspace({context:f.context,tabs:new Map([['job',{jobId:'job',page:f.page,state:f.state}]])});
    await rejected;assert(Date.now()-stopped<1000);
    await f.page.waitForFunction(()=>!!globalThis.fixtureTrace.rendered,null,{timeout:5000});
    assert.equal(f.state.paused,true);
  }finally{await f.context.close();}
}

// A permanently busy original footer reaches the bounded loading timeout without another click.
async function timeout(browser) {
  const f=await fixture(browser,{stalled:true});
  try{
    const started=Date.now();
    await assert.rejects(next(f),/application form is still loading/);
    assert(Date.now()-started>=14000&&Date.now()-started<22000);
    assert.equal(await f.page.locator('input[name="first"]').inputValue(),'Example');
  }finally{await f.context.close();}
}

// Each scenario owns only its temporary context; actual ApplyDesk and personal Chrome stay untouched.
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try{
    await readableSteps(browser);await completedOldStep(browser);await initialForm(browser);
    await cancellation(browser);await timeout(browser);
    console.log('Workday Next passed: delayed requests, busy footer, changed disabled step, validation, initial fill, cancellation and timeout.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

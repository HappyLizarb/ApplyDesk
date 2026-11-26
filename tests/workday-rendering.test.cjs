const assert=require('node:assert/strict'),fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {start,dispatch}=require('../browser/worker.cjs');
const {settleResearchPage}=require('../browser/page-settling.cjs');

// A stalled CXS read must not make the still-visible previous step look ready.
async function delayedTerms(s) {
 const html=`<main data-automation-id="applyFlowPage">${'Application navigation. '.repeat(12)}
 <section data-automation-id="applyFlowMyInfoPage"><label>First Name<input name="first"></label></section></main>
 <script>fetch('/wday/calypso/cxs/jobapplication/example/Site/termsdata').then(r=>r.json()).then(()=>{
 document.querySelector('section').outerHTML='<section data-automation-id="applyFlowLoadingPage">Loading next step</section>';
 setTimeout(()=>{document.querySelector('section').outerHTML='<section data-automation-id="applyFlowMyExpPage"><label>School<input name="school"></label></section>'},650);
 })</script>`;
 await s.context.route('**/*',async r=>{
  if(r.request().url().endsWith('/termsdata')){await new Promise(resolve=>setTimeout(resolve,2000));await r.fulfill({contentType:'application/json',body:'{}'});}
  else await r.fulfill({contentType:'text/html',body:html});
 });
 await s.page.goto('https://example.wd5.myworkdayjobs.com/en-US/Site/job/US/Engineer_JR1/apply/applyManually');
 const idle=s.page.waitForLoadState.bind(s.page);
 s.page.waitForLoadState=async()=>{}; // Simulate the bounded idle wait expiring while the employer read continues.
 try{await settleResearchPage(s.page);}finally{s.page.waitForLoadState=idle;}
 assert.equal(await s.page.locator('input[name="school"]').count(),1);
 assert.equal(await s.page.locator('input[name="first"]').count(),0);
}

// A header search field cannot satisfy a loading application step.
async function delayedStep(s) {
 const html=`<header><label>Search<input name="search"></label></header><main data-automation-id="applyFlowPage">
 ${'Application navigation. '.repeat(12)}<section data-automation-id="applyFlowLoadingPage">Loading</section></main>
 <script>setTimeout(()=>{document.querySelector('section').outerHTML='<section data-automation-id="applyFlowMyExpPage"><label>School<input name="school"></label></section>'},2200)</script>`;
 await s.context.route('**/*',r=>r.fulfill({contentType:'text/html',body:html}));
 const view=await dispatch(s,'open',{url:'https://example.wd5.myworkdayjobs.com/en-US/Site/job/US/Engineer_JR2/apply/applyManually'});
 assert(view.fields.some(field=>field.name==='school'));
}

(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-render-test-'));
 const s=await start({dataRoot:root,headless:true,allowLocal:true});
 try{
  await s.context.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<main>${'Application navigation. '.repeat(12)}</main><script>setTimeout(()=>{document.querySelector('main').insertAdjacentHTML('beforeend','<label>First Name<input name="first"></label>')},2500)</script>`}));
  const view=await dispatch(s,'open',{url:'https://example.wd5.myworkdayjobs.com/en-US/Example/job/US/Engineer_JR1/apply/applyManually'});
  assert.equal(view.fields.length,1);assert.equal(view.fields[0].name,'first');
  await delayedTerms(s);await delayedStep(s);
  console.log('Workday waits for delayed CXS terms and the new rendered step, ignoring stale fields and header inputs.');
 }finally{await dispatch(s,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});

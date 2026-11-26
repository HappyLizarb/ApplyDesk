const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {createRequire}=require('node:module');
const load=process.env.APPLYDESK_NODE_MODULES?createRequire(path.join(process.env.APPLYDESK_NODE_MODULES,'__test__.cjs')):require;
const {chromium}=load('playwright');
const {workspace,routeRequest}=require('./documents-ui.test.cjs');
const origin='http://127.0.0.1:63498';

// Intercepted settings and chat mutations never reach a provider or the installed workspace.
async function route(route,fixture) {
  const request=route.request(),url=new URL(request.url());
  if(request.method()!=='POST')return routeRequest(route,fixture);
  if(url.pathname==='/api/orchestrator/auto-start'){
    const data=request.postDataJSON();fixture.writes.push({path:url.pathname,data});
    if(fixture.reject)return route.fulfill({status:400,json:{error:'Synthetic timer save failure'}});
    fixture.data.orchestrator.auto_start={...data,next_run_at:null,last_run_at:null,pending:false};
    return route.fulfill({json:fixture.data.orchestrator.auto_start});
  }
  if(url.pathname==='/api/orchestrator/control'){
    fixture.writes.push({path:url.pathname,data:request.postDataJSON()});
    fixture.data.orchestrator.paused=true;fixture.data.orchestrator.active=null;
    fixture.data.orchestrator.auto_start.enabled=false;
    return route.fulfill({json:{paused:true}});
  }
  return routeRequest(route,fixture);
}

// Prompt height follows visual lines, shrinks, and resets only after a successful send.
async function composerChecks(page) {
  const input=page.locator('#assistant-input');
  const measure=()=>input.evaluate(node=>({height:node.getBoundingClientRect().height,scroll:node.scrollHeight,
    client:node.clientHeight,line:parseFloat(getComputedStyle(node).lineHeight)}));
  const empty=await measure();assert.ok(Math.abs(empty.height-empty.line-8)<1);
  await input.fill('One\nTwo\nThree');const three=await measure();
  assert.ok(Math.abs(three.height-three.line*3-8)<1);
  await input.fill(Array(9).fill('Another line').join('\n'));const capped=await measure();
  assert.ok(Math.abs(capped.height-capped.line*5-8)<1);assert.ok(capped.scroll>capped.client);
  await input.fill('Short');assert.equal((await measure()).height,empty.height);
  await page.setViewportSize({width:390,height:900});
  await input.fill('A prompt that wraps across several visible lines as the available width becomes narrower.');
  assert.ok((await measure()).height>empty.height);
  await page.setViewportSize({width:1440,height:900});assert.equal((await measure()).height,empty.height);
  await input.fill('One\nTwo\nThree');await page.locator('#send-assistant').click();
  await page.waitForFunction(()=>document.querySelector('#assistant-input').value==='');
  assert.equal((await measure()).height,empty.height);
}

// The timer has no task field; cancel and invalid intervals never change its stored setting.
async function timerChecks(page,fixture) {
  const clock=page.locator('#auto-start-button'),href=()=>clock.locator('use').getAttribute('href');
  assert.equal(await href(),'#icon-clock-off');
  await clock.click();assert.equal(await page.locator('#modal textarea').count(),0);
  assert.equal(await page.locator('#auto-start-interval').inputValue(),'5');
  await page.locator('#auto-start-form [name=enabled]').check();
  await page.locator('#cancel-auto-start').click();assert.equal(await href(),'#icon-clock-off');
  const before=fixture.writes.length;await clock.click();
  await page.locator('#auto-start-interval').fill('0');await page.locator('#save-auto-start').click();
  assert.equal(fixture.writes.length,before);
  await page.locator('#auto-start-interval').fill('3');
  await page.locator('#auto-start-form [name=unit]').selectOption('minutes');
  await page.locator('#auto-start-form [name=enabled]').check();await page.locator('#save-auto-start').click();
  await page.locator('#modal').waitFor({state:'hidden'});
  assert.deepEqual(fixture.writes.at(-1).data,{enabled:true,interval:3,unit:'minutes'});
  assert.equal(await href(),'#icon-clock');
  assert.match(await clock.getAttribute('aria-label'),/every 3 minutes/);
  assert.equal(fixture.writes.length,before+1,'Saving the timer does not start or submit a new task');
  await page.reload();await page.locator('#toggle-assistant').click();
  assert.equal(await href(),'#icon-clock');await clock.click();
  assert.equal(await page.locator('#auto-start-interval').inputValue(),'3');
  fixture.reject=true;await page.locator('#auto-start-interval').fill('4');
  await page.locator('#save-auto-start').click();
  await page.getByText('Synthetic timer save failure',{exact:true}).waitFor();
  assert.equal(await page.locator('#modal').isVisible(),true);
  assert.equal(await page.locator('#auto-start-interval').inputValue(),'4');
  assert.equal(fixture.data.orchestrator.auto_start.interval,3);
  fixture.reject=false;await page.locator('#cancel-auto-start').click();
}

// The normal Stop control receives the updated off state from the server.
async function stopChecks(page,fixture) {
  fixture.data.orchestrator.active={task_id:'fictional-active-work'};
  await page.reload();await page.locator('#toggle-assistant').click();
  assert.equal(await page.locator('#send-assistant').getAttribute('aria-label'),'Stop agent');
  await page.locator('#send-assistant').click();
  await page.waitForFunction(()=>document.querySelector('#auto-start-button use').getAttribute('href')==='#icon-clock-off');
  assert.equal(fixture.data.orchestrator.paused,true);
}

// Both themes keep the compact picker and interval controls within narrow viewports.
async function layoutChecks(page) {
  for(const width of [1440,390])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme:theme});
    await page.locator('#composer-agents summary').click();
    const box=await page.locator('.agent-menu-popover').boundingBox();
    assert.ok(box.width<=296&&box.height<=145&&box.x>=0);
    if(process.env.APPLYDESK_TEST_SCREENSHOTS){
      fs.mkdirSync(process.env.APPLYDESK_TEST_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,`composer-${width}-${theme}.png`)});
    }
    await page.locator('#composer-agents summary').click();await page.locator('#auto-start-button').click();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const dialog=await page.locator('#modal').boundingBox();assert.ok(dialog.x>=0&&dialog.x+dialog.width<=width);
    if(process.env.APPLYDESK_TEST_SCREENSHOTS)await page.screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,`auto-start-${width}-${theme}.png`)});
    await page.locator('#cancel-auto-start').click();
  }
}

// One fixture exercises the shipped page and real CSP without live credentials or timers.
async function main() {
  const browser=await chromium.launch({channel:'chrome',headless:true,chromiumSandbox:true});
  const fixture={data:workspace(),writes:[],reject:false};
  fixture.data.orchestrator.auto_start={enabled:false,interval:5,unit:'hours',next_run_at:null,last_run_at:null,pending:false};
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',request=>route(request,fixture));
    await page.goto(origin+'/#applications');await page.locator('#toggle-assistant').click();
    await composerChecks(page);await timerChecks(page,fixture);await stopChecks(page,fixture);await layoutChecks(page);
    assert.deepEqual(errors,[]);
    console.log('One-to-five-row composer, compact picker, timer toggle, saving, cancellation, Stop and responsive themes passed.');
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

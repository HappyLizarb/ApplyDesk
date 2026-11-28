const assert=require('node:assert/strict');
const {workspace,routeRequest,origin,browserTest}=require('./documents-ui.test.cjs');

// Delayed saves expose dropped keyboard changes without using any real account.
async function route(route,fixture) {
  if(new URL(route.request().url()).pathname!=='/api/orchestrator/role-settings')return routeRequest(route,fixture);
  const data=route.request().postDataJSON();fixture.writes.push(data);
  await new Promise(resolve=>setTimeout(resolve,180));
  if(fixture.reject)return route.fulfill({status:400,json:{error:'Synthetic save failure'}});
  fixture.data.orchestrator.settings.roles[data.role]=data.selection;
  return route.fulfill({json:{}});
}

// Each direction must show intermediate frames, land at the knob, and retain the same input.
async function motionChecks(page) {
  for(const value of ['0','4','1','3','0','4']) {
    const frames=await page.locator('#master-thinking').evaluate(async(input,value)=>{
      const fill=document.querySelector('.agent-effort-fill'),thumb=document.querySelector('.agent-effort-thumb');
      const before=fill.getBoundingClientRect().width,widths=[];
      input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
      for(let i=0;i<16;i++){
        await new Promise(requestAnimationFrame);widths.push(fill.getBoundingClientRect().width);
      }
      const knob=thumb.getBoundingClientRect(),bar=fill.getBoundingClientRect();
      return {before,widths,aligned:Math.abs(bar.right-(knob.x+knob.width/2))<1};
    },value);
    const last=frames.widths.at(-1),direction=Math.sign(last-frames.before);
    assert.ok(frames.widths.some(width=>Math.abs(width-frames.before)>1&&Math.abs(width-last)>1));
    assert.ok(frames.widths.every((width,index)=>!index||(width-frames.widths[index-1])*direction>=-.05));
    assert.ok(frames.aligned);
  }
}

// Fast keyboard changes persist the last choice while polling keeps focus and animation intact.
async function saveChecks(page,fixture) {
  const slider=page.locator('#master-thinking');
  await slider.evaluate(input=>{input.dataset.original='yes';input.value='0';input.dispatchEvent(new Event('input',{bubbles:true}));});
  const top=(await page.locator('.agent-menu-popover').boundingBox()).y;
  await slider.focus();
  for(let i=0;i<4;i++)await slider.press('ArrowRight');
  assert.equal((await page.locator('.agent-menu-popover').boundingBox()).y,top);
  await page.waitForFunction(()=>document.querySelector('#agent-menu-status').textContent==='');
  assert.equal(fixture.data.orchestrator.settings.roles.master.thinking,'xhigh');
  assert.equal(await slider.getAttribute('data-original'),'yes');
  assert.equal(await slider.evaluate(input=>input===document.activeElement),true);
  assert.equal(await slider.isEnabled(),true);
  assert.equal((await page.locator('.agent-menu-popover').boundingBox()).y,top);
  fixture.reject=true;await slider.press('ArrowLeft');
  await page.waitForFunction(()=>document.querySelector('#agent-menu-status').textContent==='');
  assert.equal(await slider.inputValue(),'4');
  assert.equal(await slider.getAttribute('aria-valuetext'),'xhigh');
  assert.match(await page.locator('#toast').textContent(),/Synthetic save failure/);
}

// The real fixture headers enforce CSP; browser motions and reduced motion share one state.
const fixture={data:workspace(),writes:[]};
fixture.data.orchestrator.account.models[0].thinking=['low','medium','high','xhigh'];
fixture.data.orchestrator.settings.roles.master.thinking='high';
browserTest('Model slider motion, queued saves, focus, failure recovery and reduced motion',
  {fixture,route,viewport:{width:1440,height:950}},async page=>{
    await page.addInitScript(()=>{window.violations=[];document.addEventListener('securitypolicyviolation',event=>window.violations.push(event.violatedDirective));});
    await page.goto(origin+'/#applications');await page.click('#toggle-assistant');await page.click('#composer-agents summary');
    await page.waitForTimeout(160);
    const panel=await page.locator('.agent-menu-popover').boundingBox(),rail=await page.locator('.agent-effort-track').boundingBox();
    assert.ok(panel.height<=158&&panel.y+panel.height-rail.y-rail.height<=22,'The compact picker has no reserved blank footer');
    assert.equal(await page.locator('#master-thinking').inputValue(),'3');
    assert.ok((await page.locator('.agent-effort-fill').boundingBox()).width>150);
    assert.equal(await page.locator('#application-run-status').count(),0);
    await motionChecks(page);await saveChecks(page,fixture);
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('.agent-effort-fill').evaluate(node=>getComputedStyle(node).transitionDuration),'0s');
    assert.deepEqual(await page.evaluate(()=>window.violations),[]);
  });

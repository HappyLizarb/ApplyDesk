const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {createRequire}=require('node:module');
const load=process.env.APPLYDESK_NODE_MODULES?createRequire(path.join(process.env.APPLYDESK_NODE_MODULES,'__test__.cjs')):require;
const {chromium}=load('playwright');
const {workspace,routeRequest}=require('./documents-ui.test.cjs');
const origin='http://127.0.0.1:63498';

// Only fictional files and explicit local mutations exist in this browser fixture.
function fixture() {
  const data=workspace();
  data.profile_schema=10;
  data.orchestrator.account.accounts=[{id:'fixture-account',label:'example@example.test · Sample'}];
  data.orchestrator.account.active='fixture-account';data.orchestrator.account.chatgpt_active='fixture-account';
  data.orchestrator.account.api_keys_saved={openai:false,anthropic:false,google:false,xai:false};
  data.orchestrator.account.models[0].thinking=['low','medium','high','xhigh'];
  data.profile.websites=[{type:'LinkedIn',url:'https://linkedin.com/in/test'},{type:'Portfolio',url:'https://example.test'}];
  data.profile.identity={legal_name:'Example Person',given_name:'Example',family_name:'Person',email:'example@example.test'};
  data.profile.authorizations=[{country:'US',sponsorship_required:'no'}];
  data.profile.revision=4;
  data.profile.education=[{institution:'Example University',degree:'Bachelor of Arts',field:'History and Geography',gpa:'',gpa_scale:'',start:'2013',end:'2017',coursework:'Cartography, Social History, Public Archives'}];
  data.profile.projects=[{name:'Existing project',details:'Previously saved fact'}];
  data.profile.transcript_id='transcript';
  data.uploads=['profile','transcript','old-resume'].map(id=>({id,kind:id==='old-resume'?'resume':id,filename:id+'.pdf',source_path:id+'/original.pdf',pdf_path:id+'/original.pdf'}));
  data.profile_imports=[data.uploads[0]];
  return {data,writes:[],failDelete:false};
}

// Month and year remain explicit, and optional GPA survives the real form's save and reload.
async function educationChecks(page,fixture) {
  await page.goto(origin+'/#profile');
  const row=page.locator('[data-row="education"]');
  assert.deepEqual(await row.locator('.entry-body > div').evaluateAll(nodes=>nodes.map(node=>node.className)),
    ['entry-title','entry-line entry-education-meta','entry-line entry-dates','entry-coursework']);
  assert.equal(await row.locator('[name="education.0.start.month"]').inputValue(),'');
  assert.equal(await row.locator('[name="education.0.gpa"]').getAttribute('required'),null);
  await row.locator('[name="education.0.gpa"]').fill('91');
  await row.locator('[name="education.0.gpa_scale"]').fill('100');
  await row.locator('[name="education.0.start.month"]').selectOption('08');
  await row.locator('[name="education.0.start.day"]').fill('20');
  await row.locator('[name="education.0.end.month"]').selectOption('05');
  await page.getByLabel('Are you 18 or older?').selectOption('yes');
  await page.getByLabel('Application answers',{exact:true}).fill('I request the top of the posted base salary range.');
  await page.locator('#profile-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('Changes saved'));
  assert.equal(fixture.data.profile.education[0].gpa,'91');
  assert.equal(fixture.data.profile.education[0].gpa_scale,'100');
  assert.equal(fixture.data.profile.education[0].start,'2013-08-20');
  assert.equal(fixture.data.profile.education[0].end,'2017-05');
  await page.reload();
  assert.equal(await row.locator('[name="education.0.start.day"]').inputValue(),'20');
  assert.equal(await row.locator('[name="education.0.end.day"]').inputValue(),'');
  assert.equal(await page.getByLabel('Are you 18 or older?').inputValue(),'yes');
  assert.deepEqual(fixture.data.profile.preferences.application_answers,['I request the top of the posted base salary range.']);
  assert.equal(await row.locator('[name="education.0.gpa"]').inputValue(),'91');
  assert.equal(await row.locator('[name="education.0.gpa_scale"]').inputValue(),'100');
  assert.equal(await row.locator('[name="education.0.end.month"]').inputValue(),'05');
  assert.equal(await row.locator('[name="education.0.end.year"]').inputValue(),'2017');
}

// Model selection and key management are simulated without contacting providers.
async function route(route,fixture) {
  const url=new URL(route.request().url()),data=route.request().postDataJSON();
  if(route.request().method()==='POST'&&url.pathname==='/api/profile'){
    fixture.writes.push({path:url.pathname,data});
    fixture.data.profile={...data,revision:fixture.data.profile.revision+1,transcript_id:fixture.data.profile.transcript_id};
    return route.fulfill({json:fixture.data.profile});
  }
  if(route.request().method()!=='POST'||!url.pathname.startsWith('/api/orchestrator/'))return routeRequest(route,fixture);
  fixture.writes.push({path:url.pathname,data});
  const engine=fixture.data.orchestrator;
  if(url.pathname.endsWith('/role-settings'))engine.settings.roles[data.role]=data.selection;

  else if(url.pathname.endsWith('/api-key')) {
    engine.account.api_keys_saved[data.provider]=true;engine.account.source=data.provider+'_api';
    fixture.data.tasks=[{id:'key-check',status:'done',result:{}}];
    return route.fulfill({json:{task:'key-check'}});
  } else if(url.pathname.endsWith('/api-key/remove'))engine.account.api_keys_saved[data.provider]=false;
  else if(url.pathname.endsWith('/source'))engine.account.source=data.source;
  else throw new Error('Unexpected provider mutation '+url.pathname);
  return route.fulfill({json:{}});
}

// Repeated role choices stay independent through edits, deletion, saving and reopening.
async function rolePreferenceChecks(page,fixture) {
  await page.goto(origin+'/#profile');
  await page.click('[data-add="industries"]');
  await page.locator('[name="industries.1.name"]').fill('Museum operations');
  await page.locator('[name="industries.1.years"]').fill('3');
  const picker=page.locator('.role-preference-row').nth(1).locator('.level-picker');
  await picker.locator('summary').click();
  await picker.locator('[value="Senior"]').check();await picker.locator('[value="Manager"]').check();
  await picker.locator('[value="Manager"]').press('Escape');
  assert.equal(await picker.getAttribute('open'),null);
  await page.click('[data-add="industries"]');
  await page.locator('[name="industries.2.name"]').fill('Remove this role');
  await page.click('[data-remove="industries"][data-index="2"]');
  assert.deepEqual(await picker.locator('input:checked').evaluateAll(nodes=>nodes.map(node=>node.value)),['Senior','Manager']);
  await page.locator('#profile-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('Changes saved'));
  assert.deepEqual(fixture.data.profile.industries,[{name:'Publishing',years:'0',levels:['Entry level']},
    {name:'Museum operations',years:'3',levels:['Senior','Manager']}]);
  assert.deepEqual(fixture.data.profile.projects,[{name:'Existing project',details:'Previously saved fact'}]);
  await page.reload();
  assert.deepEqual(await page.locator('.role-preference-row .level-tags').allTextContents(),['Entry level','SeniorManager']);
  assert.equal(await page.getByRole('heading',{name:'Projects',exact:true}).count(),0);
}

// Preview responds to keyboard input immediately; changing the slider saves one selection.
async function sliderChecks(page,fixture) {
  await page.goto(origin+'/#applications');
  await page.click('#toggle-assistant');await page.click('#composer-agents summary');
  assert.equal(await page.locator('.agent-effort-dot').count(),5);
  const slider=page.locator('#master-thinking');
  assert.ok((await page.locator('.agent-effort-fill').boundingBox()).width>=16,'The fill loads under the app security policy');
  await slider.focus();await slider.press('ArrowRight');
  await page.waitForFunction(()=>document.querySelector('#master-thinking').getAttribute('aria-valuetext')==='low');
  assert.equal(await page.locator('.agent-effort-dot.selected').count(),1);
  assert.equal(await page.locator('.agent-effort-dot.selected').getAttribute('title'),'low');
  await page.waitForFunction(()=>!document.querySelector('#master-thinking').disabled);
  assert.equal(fixture.data.orchestrator.settings.roles.master.thinking,'low');
  assert.equal(fixture.writes.filter(row=>row.path.endsWith('/role-settings')).length,1);
  const fill=page.locator('.agent-effort-fill'),lowWidth=(await fill.boundingBox()).width;
  assert.ok((await page.locator('.agent-menu-popover').boundingBox()).width<=296);
  assert.equal((await fill.boundingBox()).height,24);
  const heading=await page.locator('.agent-slider-heading').boundingBox(),model=await page.locator('#master-model').boundingBox();
  assert.ok(model.y-heading.y-heading.height>=8);
  await slider.evaluate(input=>{input.value=input.max;input.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.waitForTimeout(160);
  assert.ok((await fill.boundingBox()).width>lowWidth);
  if(process.env.APPLYDESK_TEST_SCREENSHOTS){
    fs.mkdirSync(process.env.APPLYDESK_TEST_SCREENSHOTS,{recursive:true});
    await page.locator('.agent-menu-popover').screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,'thinking-slider.png')});
    await page.emulateMedia({colorScheme:'dark'});
    await page.locator('.agent-menu-popover').screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,'thinking-slider-dark.png')});
  }
  await slider.evaluate(input=>{input.value='1';input.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.setViewportSize({width:390,height:950});
  const compact=await page.locator('.agent-menu-popover').boundingBox();
  assert.ok(compact.width<=296&&compact.x>=0);
  await page.setViewportSize({width:1440,height:950});
}

// Provider switching clears unsaved keys; Cursor cannot trigger a misleading key save.
async function providerChecks(page,fixture) {
  await page.goto(origin+'/#settings');
  assert.equal(await page.getByRole('heading',{name:'API Keys',exact:true}).count(),1);
  assert.equal(await page.getByRole('checkbox',{name:/required employer terms|accept required employer/i}).count(),0);
  const row=page.locator('.api-key-row').first();
  await row.locator('input').fill('test-unsent-key');
  await row.locator('select').selectOption('cursor');
  assert.equal(await row.locator('input').inputValue(),'');
  assert.equal(await row.locator('[type=submit]').isDisabled(),true);
  assert.match(await row.locator('[data-key-status]').textContent(),/unavailable/);
  await row.locator('select').selectOption('xai');
  await row.locator('input').fill('test-grok-key');await row.locator('[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#connection-source').value==='xai_api');
  assert.equal(await row.locator('input').inputValue(),'');
  assert.deepEqual(fixture.writes.find(item=>item.path.endsWith('/api-key')).data,{key:'test-grok-key',provider:'xai'});
  await page.click('#add-api-key');
  assert.equal(await page.locator('.api-key-row').count(),2);
  assert.equal(await page.locator('.api-key-row').last().locator('select option[value=xai]').evaluate(option=>option.disabled),true);
  await row.locator('[data-remove-key]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.api-key-row').length===1);
  assert.equal(fixture.data.orchestrator.account.api_keys_saved.xai,false);
  await page.locator('#connection-source').selectOption('chatgpt');
  await page.waitForFunction(()=>document.querySelector('#connection-source').value==='chatgpt'&&!document.querySelector('#connection-source').disabled);
}

// GitHub is editable in the compact list and deleting a source preserves unsaved profile text.
async function websiteAndUploadChecks(page,fixture) {
  await page.goto(origin+'/#profile');
  const personal=page.locator('#profile-form > .panel').filter({has:page.getByRole('heading',{name:'Personal details',exact:true})});
  assert.equal(await personal.locator('[data-row="industries"]').count(),0);
  assert.equal(await personal.locator('.website-list-row').count(),2);
  assert.deepEqual(await personal.locator('.profile-detail-list h3').allTextContents(),['Websites']);
  assert.equal(await page.locator('.website-list-row').count(),2);
  assert.equal(await page.getByRole('heading',{name:'Projects',exact:true}).count(),0);
  const preferences=page.locator('#profile-form > .panel').filter({has:page.getByRole('heading',{name:'Skills and preferences',exact:true})});
  assert.equal(await preferences.locator('[data-row="industries"]').count(),1);
  assert.deepEqual(await preferences.locator('input[name="industries.0.levels"]:checked').evaluateAll(nodes=>nodes.map(node=>node.value)),['Entry level']);
  await page.locator('[name="websites.1.type"]').selectOption('GitHub');
  await page.locator('[name="websites.1.url"]').fill('https://github.com/example');
  await page.locator('[name="identity.legal_name"]').fill('Unsaved Test Name');
  await page.click('[data-delete-upload="profile"]');await page.click('#delete-document-confirm');
  await page.waitForSelector('#modal',{state:'hidden'});
  assert.equal(await page.locator('[data-delete-upload="profile"]').count(),0);
  assert.equal(await page.locator('[name="identity.legal_name"]').inputValue(),'Unsaved Test Name');
  assert.equal(await page.locator('[name="websites.1.type"]').inputValue(),'GitHub');
  await page.goto(origin+'/#documents');
  await page.click('[data-delete-document="transcript"]');await page.click('#delete-document-confirm');
  await page.waitForSelector('#modal',{state:'hidden'});
  assert.equal(fixture.data.profile.transcript_id,null);
  assert.equal(await page.locator('[data-delete-document="old-resume"]').count(),1);
}

// Real rendering checks both themes, narrow screens and the supplied compact layout direction.
async function layoutChecks(page) {
  for(const width of [1440,390])for(const theme of ['light','dark'])for(const view of ['settings','profile','documents']) {
    await page.setViewportSize({width,height:950});await page.emulateMedia({colorScheme:theme});
    await page.goto(origin+'/#'+view);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${view}: ${width}, ${theme}`);
    assert.equal(await page.locator('#toast.danger:not([hidden])').count(),0,`${view} has an unexpected error`);
    if(process.env.APPLYDESK_TEST_SCREENSHOTS) {
      fs.mkdirSync(process.env.APPLYDESK_TEST_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,`${view}-${width}-${theme}.png`),fullPage:true});
    }
  }
}

// The isolated Chrome profile is always closed, including when an assertion fails.
async function main() {
  const browser=await chromium.launch({channel:'chrome',headless:true,chromiumSandbox:true});
  const data=fixture(),errors=[];
  try {
    const page=await browser.newPage({viewport:{width:1440,height:950}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',request=>route(request,data));
    await sliderChecks(page,data);await providerChecks(page,data);
    await websiteAndUploadChecks(page,data);await rolePreferenceChecks(page,data);await educationChecks(page,data);await layoutChecks(page);
    assert.deepEqual(errors,[]);
    console.log('Slider stops, API key rows, Cursor limitation, GitHub websites, file deletion and responsive themes passed.');
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {createRequire}=require('node:module');
const load=process.env.APPLYDESK_NODE_MODULES?createRequire(path.join(process.env.APPLYDESK_NODE_MODULES,'__test__.cjs')):require;
const {chromium}=load('playwright');
const {workspace,routeRequest}=require('./documents-ui.test.cjs');
const origin='http://127.0.0.1:63498';

// Self-reported status is tested only with fictional profiles and intercepted local saves.
async function route(route,fixture) {
  if(route.request().method()==='POST'&&new URL(route.request().url()).pathname==='/api/profile'){
    const data=route.request().postDataJSON();fixture.writes.push(data);
    fixture.data.profile={...data,revision:fixture.data.profile.revision+1};
    return route.fulfill({json:fixture.data.profile});
  }
  return routeRequest(route,fixture);
}

// Adding entries stays keyboard accessible and below existing rows in every repeated section.
async function addChecks(page) {
  const controls=page.locator('#profile-form [data-add]');
  assert.deepEqual(await controls.allTextContents(),['Add website','Add education','Add experience','Add work authorization','Add language','Add role preference']);
  assert.equal(await page.locator('#profile-form .panel-heading [data-add]').count(),0);
  assert.ok(await controls.evaluateAll(buttons=>buttons.every(button=>
    [...button.parentElement.querySelectorAll('[data-row]')].every(row=>row.compareDocumentPosition(button)&Node.DOCUMENT_POSITION_FOLLOWING))));
  await page.locator('[data-add="authorizations"]').click();
  await page.locator('[name="authorizations.1.country"]').selectOption('CA');
  await page.locator('[name="authorizations.1.sponsorship_required"]').selectOption('no');
  assert.equal(await page.locator('#modal').isVisible(),false);
  assert.equal(await page.locator('[data-row="authorizations"]').nth(1).locator('[data-us-status]').isVisible(),false);
}

// Confirmation tags only claim saved, explicitly reviewed profile facts.
async function confirmationChecks(page) {
  await page.locator('[name=confirmed]').check();
  assert.deepEqual(await page.locator('[data-profile-confirmation]').allTextContents(),Array(6).fill('Draft'));
  await page.locator('#profile-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-profile-confirmation].ready').length===6);
  await page.reload();
  assert.deepEqual(await page.locator('[data-profile-confirmation]').allTextContents(),Array(6).fill('Confirmed'));
}

// Cancellation preserves other edits, and choosing a status changes only the unsaved draft.
async function statusChecks(page,fixture) {
  await page.locator('[name="identity.legal_name"]').fill('Unsaved Example');
  await page.locator('[name="authorizations.0.sponsorship_required"]').selectOption('no');
  const dialog=page.locator('#modal');await dialog.waitFor({state:'visible'});
  assert.deepEqual(await dialog.locator('option').evaluateAll(nodes=>nodes.map(node=>node.value)),
    ['','us_citizen','us_national','permanent_resident','f1_opt','f1_stem_opt','f1_cpt','other_work_authorized','other_unsure']);
  await page.locator('[data-cancel-status]').click();
  assert.equal(await page.locator('[name="authorizations.0.immigration_status"]').inputValue(),'');
  assert.equal(await page.locator('[name="identity.legal_name"]').inputValue(),'Unsaved Example');
  await page.locator('[data-row="authorizations"]').first().locator('[data-us-status]').click();
  await page.locator('#us-immigration-status').selectOption('f1_opt');
  await page.locator('#us-work-restrictions').fill('Work related to my field of study.');
  await page.locator('[data-use-status]').click();
  assert.equal(fixture.writes.length,1);
  assert.equal(await page.locator('[name="confirmed"]').isChecked(),false);
  await page.locator('#profile-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('Changes saved'));
  assert.equal(fixture.data.profile.authorizations[0].status,'unknown');
  assert.equal(fixture.data.profile.authorizations[0].immigration_status,'f1_opt');
  await page.reload();
  assert.equal(await dialog.isVisible(),false);
  await page.locator('[data-row="authorizations"]').first().locator('[data-us-status]').click();
  assert.equal(await page.locator('#us-work-restrictions').inputValue(),'Work related to my field of study.');
  await page.locator('#us-immigration-status').selectOption('other_work_authorized');
  await page.locator('#us-work-restrictions').fill('Example user-provided details');
  await page.locator('[data-use-status]').click();
}

// Inactive US details cannot linger after a country or sponsorship change.
async function clearingChecks(page) {
  await page.locator('[name="authorizations.0.country"]').selectOption('GB');
  assert.equal(await page.locator('[name="authorizations.0.immigration_status"]').inputValue(),'');
  assert.equal(await page.locator('[name="authorizations.0.restrictions"]').inputValue(),'');
  await page.locator('[name="authorizations.0.country"]').selectOption('US');
  await page.locator('#modal').waitFor({state:'visible'});
  assert.equal(await page.locator('#us-immigration-status').inputValue(),'');
  await page.locator('#us-immigration-status').selectOption('us_citizen');
  await page.locator('[data-use-status]').click();
  await page.locator('[name="authorizations.0.sponsorship_required"]').selectOption('yes');
  assert.equal(await page.locator('[name="authorizations.0.immigration_status"]').inputValue(),'');
  assert.equal(await page.locator('[data-row="authorizations"]').first().locator('[data-us-status]').isVisible(),false);
}

// Both themes and narrow viewports keep the status dialog inside the visible page.
async function visualChecks(page) {
  for(const width of [1440,390])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme:theme});
    await page.locator('[name="authorizations.0.sponsorship_required"]').selectOption('no');
    if(!await page.locator('#modal').isVisible())await page.locator('[data-row="authorizations"]').first().locator('[data-us-status]').click();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const box=await page.locator('#modal').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);
    if(process.env.APPLYDESK_TEST_SCREENSHOTS){
      fs.mkdirSync(process.env.APPLYDESK_TEST_SCREENSHOTS,{recursive:true});
      await page.screenshot({path:path.join(process.env.APPLYDESK_TEST_SCREENSHOTS,`authorization-${width}-${theme}.png`)});
    }
    await page.locator('[data-cancel-status]').click();
  }
}

// A single No resolves both answers, while veterans and legacy profiles retain separate disclosure choices.
async function veteranChecks(page,fixture) {
  const service=page.locator('[name=veteran_service]');
  const protectedStatus=page.locator('[name=veteran]');
  await service.selectOption('yes');
  await protectedStatus.selectOption('yes');
  await service.selectOption('no');
  assert.equal(await protectedStatus.isVisible(),false);
  await page.locator('#profile-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('Changes saved'));
  assert.equal(fixture.data.profile.preferences.veteran_service,'no');
  assert.equal(fixture.data.profile.preferences.veteran,'no');
  await page.reload();
  assert.equal(await protectedStatus.isVisible(),false);
  await service.selectOption('yes');
  assert.equal(await protectedStatus.inputValue(),'unknown');
  await protectedStatus.selectOption('decline');
  await service.selectOption('no');
  await service.selectOption('unknown');
  assert.equal(await protectedStatus.inputValue(),'decline');
}

// Browser state stays isolated from the installed application and all provider accounts.
async function main() {
  const browser=await chromium.launch({channel:'chrome',headless:true,chromiumSandbox:true});
  const fixture={data:workspace(),writes:[]},errors=[];
  fixture.data.profile.revision=1;
  fixture.data.profile.authorizations=[{country:'US',sponsorship_required:'yes',status:'unknown',immigration_status:'',restrictions:''}];
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',request=>route(request,fixture));
    await page.goto(origin+'/#profile');
    assert.deepEqual(await page.locator('[data-profile-confirmation]').allTextContents(),Array(6).fill('Confirmed'));
    await addChecks(page);
    assert.deepEqual(await page.locator('[data-profile-confirmation]').allTextContents(),Array(6).fill('Draft'));
    await confirmationChecks(page);await statusChecks(page,fixture);
    await clearingChecks(page);await visualChecks(page);await veteranChecks(page,fixture);assert.deepEqual(errors,[]);
    console.log('Bottom Add links, US-only status, cancellation, draft saving, reload, clearing and responsive dialog passed.');
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

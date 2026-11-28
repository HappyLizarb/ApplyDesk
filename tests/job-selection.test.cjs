const assert=require('node:assert/strict');
const {workspace,routeRequest,origin,browserTest,screenshot}=require('./documents-ui.test.cjs');

// Real table controls operate on fictional jobs; every request stays inside this fixture.
function fixture() {
  const data=workspace(),base=data.jobs[0];
  data.jobs=[{...base,id:'editor',title:'Copy Editor',status:'draft'},
    {...base,id:'museum',title:'Museum Curator',status:'draft'},
    {...base,id:'submitted',title:'Process Engineer',status:'submitted'}];
  return {data,writes:[],failRemoval:false,removalWait:null};
}

// Failed deletion leaves selections intact, while success removes exactly the requested IDs.
async function route(route,fixture) {
  if(new URL(route.request().url()).pathname==='/api/job/status'){
    const {id,status}=route.request().postDataJSON();
    fixture.data.jobs.find(job=>job.id===id).manual_status=status;
    return route.fulfill({json:{id,manual_status:status}});
  }
  if(new URL(route.request().url()).pathname!=='/api/jobs/remove')return routeRequest(route,fixture);
  const data=route.request().postDataJSON();
  fixture.writes.push(data);
  if(fixture.failRemoval)return route.fulfill({status:400,json:{error:'Fixture removal failed'}});
  if(fixture.removalWait)await fixture.removalWait;
  fixture.data.jobs=fixture.data.jobs.filter(job=>!data.ids.includes(job.id));
  return route.fulfill({json:{removed:data.ids.length}});
}

// Keyboard toggling and partial state reflect visible rows, preserving selections behind filters.
async function selectionChecks(page,fixture) {
  const all=page.locator('#select-all-jobs'),rows=page.locator('[data-select-job]');
  assert.equal(await all.isChecked(),false);
  await rows.first().check();
  assert.equal(await all.evaluate(input=>input.indeterminate),true);
  await all.focus();await all.press('Space');
  assert.equal(await page.locator('[data-select-job]:checked').count(),3);
  await all.press('Space');
  assert.equal(await page.locator('[data-select-job]:checked').count(),0);
  await page.click('[data-filter="submitted"]');await all.check();
  await page.click('[data-filter="draft"]');
  assert.equal(await all.isChecked(),false);
  await all.check();
  assert.equal(await page.locator('#remove-jobs').textContent(),'Remove selected (3)');
  await all.uncheck();
  assert.equal(await page.locator('#remove-jobs').textContent(),'Remove selected (1)');
  await page.locator('#search-jobs').fill('Editor');await all.check();
  await page.locator('#search-jobs').fill('No matching job');
  assert.equal(await all.isDisabled(),true);
  assert.equal(await all.isChecked(),false);
  await page.locator('#search-jobs').fill('');
  assert.equal(await all.evaluate(input=>input.indeterminate),true);
  fixture.data.jobs[0].progress='Updated during polling';
  await all.focus();await page.evaluate(()=>import('/ui.js').then(module=>module.refresh()));
  assert.equal(await all.evaluate(input=>input===document.activeElement),true);
  await page.click('[data-filter="all"]');
  assert.equal(await page.locator('[data-select-job="submitted"]').isChecked(),true);
  await all.check();await all.uncheck();
  assert.equal(await page.locator('#remove-jobs').isVisible(),false);
}

// A poll never selects a newly arrived job, and removed rows no longer count as selected.
async function pollingChecks(page,fixture) {
  const all=page.locator('#select-all-jobs');
  await all.check();
  fixture.data.jobs.push({...fixture.data.jobs[0],id:'new',title:'New job'});
  await page.evaluate(()=>import('/ui.js').then(module=>module.refresh()));
  assert.equal(await all.evaluate(input=>input.indeterminate),true);
  assert.equal(await page.locator('[data-select-job="new"]').isChecked(),false);
  fixture.data.jobs=fixture.data.jobs.filter(job=>job.id!=='submitted');
  await page.evaluate(()=>import('/ui.js').then(module=>module.refresh()));
  assert.equal(await page.locator('#remove-jobs').textContent(),'Remove selected (2)');
  await all.check();await all.uncheck();
}

// Bulk removal disables its controls while pending and leaves them usable after failure.
async function removalChecks(page,fixture) {
  await page.click('[data-filter="draft"]');await page.locator('#select-all-jobs').check();
  fixture.failRemoval=true;
  await page.click('#remove-jobs');await page.getByText('Fixture removal failed',{exact:true}).waitFor();
  assert.equal(await page.locator('#select-all-jobs').isChecked(),true);
  fixture.failRemoval=false;
  let release;
  fixture.removalWait=new Promise(resolve=>{release=resolve;});
  await page.click('#remove-jobs');
  await page.waitForFunction(()=>document.querySelector('#select-all-jobs').disabled);
  assert.equal(await page.locator('[data-select-job]:enabled').count(),0);
  release();await page.getByText('3 applications removed.',{exact:true}).waitFor();
  assert.deepEqual(fixture.writes.map(write=>write.ids.sort()),[['editor','museum','new'],['editor','museum','new']]);
  assert.equal(await page.locator('#select-all-jobs').isDisabled(),true);
  assert.equal(await page.locator('#remove-jobs').isVisible(),false);
}

// Screenshots use a separate disposable page so selection tests keep their own state.
async function layoutChecks(browser,fixture) {
  fixture.data=workspace();
  const page=await browser.newPage({viewport:{width:1440,height:950}});
  await page.route('**/*',request=>routeRequest(request,fixture));
  await page.emulateMedia({colorScheme:'dark'});await page.goto(origin+'/#applications');
  const header=await page.locator('#select-all-jobs').boundingBox();
  const row=await page.locator('[data-select-job]').boundingBox();
  assert.ok(Math.abs(header.x-row.x)<1&&header.y<row.y,'Header checkbox aligns above row checkboxes');
  await screenshot(page.locator('.applications-panel'),'job-selection.png');
  await page.close();
}

// No employer, signed-in account or personal workspace is accessed by this browser check.
const data=fixture();
browserTest('Job selection, filtering, polling, removal and header alignment',
  {fixture:data,route},async (page,browser)=>{
    await page.goto(origin+'/#applications');
    const status=page.getByRole('combobox',{name:/Status for Copy Editor/});
    assert.deepEqual(await status.locator('option').allTextContents(),['Not started','Draft','Needs input','Submitted','Skipped']);
    await status.selectOption('skipped');await page.click('[data-filter="skipped"]');
    assert.equal(await status.inputValue(),'skipped');
    await page.reload();await status.waitFor();assert.equal(await status.inputValue(),'skipped');
    await status.selectOption('draft');
    await selectionChecks(page,data);await pollingChecks(page,data);await removalChecks(page,data);
    await layoutChecks(browser,data);
  });

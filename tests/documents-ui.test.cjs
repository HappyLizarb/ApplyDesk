const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const load = process.env.APPLYDESK_NODE_MODULES ? createRequire(path.join(process.env.APPLYDESK_NODE_MODULES, '__test__.cjs')) : require;
const {chromium} = load('playwright');
const origin = 'http://127.0.0.1:63498', web = path.resolve(__dirname, '../web');

// Fictional documents and an existing application exercise deletion without personal files.
function workspace() {
  const resume = {id: 'resume', kind: 'resume', filename: 'Resume <example>.pdf', category: 'General', source_path: 'resume/original.pdf', pdf_path: 'resume/original.pdf'};
  const template = {id: 'template', kind: 'cover_template', filename: 'Letter template.docx', confirmed: true, source_path: 'template/original.docx', pdf_path: 'template/preview.pdf'};
  return {
    settings: {cover_template_id: 'template'}, templates: [template], resumes: [resume], resume_categories: ['General', 'Publishing'],
    documents: [], library: [], chat_tasks: [], profile_imports: [], tasks: [], discovery: {}, profile_schema: 10,
    profile: {identity: {}, education: [], experiences: [], projects: [], authorizations: [], websites: [], skills: [],
      industries: [{name: 'Publishing', years: '0', levels: ['Entry level']}], preferences: {levels: ['Entry level'], disability: 'unknown', veteran: 'unknown'}, confirmed: true},
    jobs: [{id: 'job', company: 'Example', title: 'Publishing Assistant', status: 'submitted', resume_id: 'resume', cover_state: 'not_required', history: []}],
    recommendation_preferences: {handshake: {enabled: true}, linkedin: {enabled: false}},
    recommendations: {handshake: {status: 'complete', connected: true}},
    orchestrator: {account: {connected: true, accounts: [], models: [{id: 'test-model', name: 'GPT-6.1-Sol', thinking: ['medium']}], source: 'chatgpt'}, signin: {status: 'idle'}, settings: {
      roles: Object.fromEntries(['master'].map(role => [role, {model: 'test-model', thinking: 'default'}])), automatic: true},
      paused: false, active: null, current_run: null, error: '', messages: [], runs: [], application_runs: []},
  };
}

// The complete app is served through intercepted routes; unexpected network access fails.
async function routeRequest(route, fixture) {
  const request = route.request(), url = new URL(request.url());
  assert.equal(url.origin, origin);
  if (url.pathname === '/api/state') return route.fulfill({json: fixture.data});
  if (url.pathname === '/api/document') return route.fulfill({json: (fixture.data.uploads||[]).find(row=>row.id===url.searchParams.get('id'))||(fixture.data.resumes||[]).find(row=>row.id===url.searchParams.get('id'))||{id:'old-cover',kind:'document',document_type:'cover',title:'Saved letter',pdf_path:'old-cover/letter.pdf',docx_path:'old-cover/letter.docx',blocks:[]}});
  if (url.pathname.startsWith('/asset/')) return route.fulfill({body:'Synthetic document preview',contentType:'text/plain'});
  if (request.method() === 'POST') {
    const data = request.postDataJSON();
    fixture.writes.push({path: url.pathname, data});
    if (url.pathname === '/api/document/delete') {
      if (fixture.failDelete) return route.fulfill({status: 400, json: {error: 'Pause the agent before deleting a file.'}});
      fixture.data.resumes = fixture.data.resumes.filter(item => item.id !== data.document_id);
      fixture.data.templates = fixture.data.templates.filter(item => item.id !== data.document_id);
      fixture.data.uploads=(fixture.data.uploads||[]).filter(item=>item.id!==data.document_id);
      fixture.data.profile_imports=fixture.data.profile_imports.filter(item=>item.id!==data.document_id);
      if(fixture.data.profile.transcript_id===data.document_id)fixture.data.profile.transcript_id=null;
      fixture.data.jobs.forEach(job=>{if(job.resume_id===data.document_id)job.resume_id=null;});
      if (fixture.data.settings.cover_template_id === data.document_id) fixture.data.settings.cover_template_id = '';
      return route.fulfill({json: {removed: true, application_copies_kept: false, cleanup_pending: data.document_id === 'template'}});
    }
    if (url.pathname === '/api/orchestrator/send') return route.fulfill({json: {status: 'queued', message_id: 'message'}});
    if (url.pathname === '/api/orchestrator/show-application') {
      fixture.data.tasks = [{id: 'show-application', status: 'done', result: {visible: true}}];
      return route.fulfill({json: {task: 'show-application'}});
    }
    if (url.pathname === '/api/recommendations/begin') return route.fulfill({json: {platform: data.platform, run_id: 'connect'}});
    throw new Error('Unexpected mutation: ' + url.pathname);
  }
  const file = path.resolve(web, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
  assert.ok(file.startsWith(web + path.sep));
  const headers={'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"};
  return route.fulfill({body: fs.readFileSync(file), headers, contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
}

// Cancellation and failures keep the row; success removes only the selected library entry.
async function deletionChecks(page, fixture) {
  await page.goto(origin + '/#documents');
  await page.waitForSelector('[data-delete-document="resume"]');
  await page.click('[data-delete-document="resume"]');
  assert.ok((await page.locator('#modal').textContent()).includes('Resume <example>.pdf'));
  assert.equal(await page.locator('#modal example').count(), 0);
  await page.click('#delete-document-cancel');
  assert.equal(fixture.writes.length, 0);
  fixture.failDelete = true;
  await page.click('[data-delete-document="resume"]');
  await page.click('#delete-document-confirm');
  await page.getByText('Pause the agent before deleting a file.', {exact: true}).waitFor();
  assert.equal(await page.locator('[data-delete-document="resume"]').count(), 1);
  fixture.failDelete = false;
  await page.click('#delete-document-confirm');
  await page.waitForSelector('#modal', {state: 'hidden'});
  assert.equal(await page.locator('[data-delete-document="resume"]').count(), 0);
  assert.equal(fixture.data.jobs[0].resume_id, null);
  assert.equal(await page.locator('#import-cover-template,#cover-template-form,[data-template]').count(), 0);
  assert.equal(await page.getByText('Cover letter template', {exact: true}).count(), 0);
  assert.equal(fixture.data.templates[0].id, 'template');

}

// Manual search uses the chat's requested scope rather than directly bulk-importing a feed.
async function searchChecks(page, fixture) {
  fixture.data.jobs = [];
  fixture.data.recommendations = {};
  fixture.data.recommendation_preferences = {};
  await page.goto(origin + '/#applications');
  await page.reload();
  await page.click('[data-find-first]');
  await page.waitForSelector('#assistant-panel:visible');
  const request = fixture.writes.find(item => item.path === '/api/orchestrator/send');
  assert.ok(request, 'Find jobs queues a scoped chat search');
  assert.match(request.data.message, /Publishing/);
  assert.match(request.data.message, /Entry level/);
  assert.match(request.data.message, /Google X-ray/);
  assert.equal(fixture.writes.filter(item => item.path === '/api/recommendations/begin').length, 0);
  await page.goto(origin + '/#profile');
  assert.equal(await page.locator('#job-platforms,[data-source-start]').count(), 0);
}

// Extra row actions must fit in both themes without horizontal page overflow.
async function layoutChecks(page, fixture) {
  fixture.data = workspace();
  for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
    await page.setViewportSize({width, height: 950});
    await page.emulateMedia({colorScheme: theme});
    await page.goto(origin + '/#documents');
    await page.reload();
    await page.waitForSelector('[data-delete-document="resume"]');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Document overflow: ${width}, ${theme}`);
    if (process.env.APPLYDESK_TEST_SCREENSHOTS) {
      fs.mkdirSync(process.env.APPLYDESK_TEST_SCREENSHOTS, {recursive: true});
      await page.screenshot({path: path.join(process.env.APPLYDESK_TEST_SCREENSHOTS, `documents-${width}-${theme}.png`)});
      await page.click('#toggle-assistant');
      await page.locator('#assistant-form').screenshot({path: path.join(process.env.APPLYDESK_TEST_SCREENSHOTS, `composer-${width}-${theme}.png`)});
    }
  }
}

// Only genuinely absent saved models receive an unavailable placeholder.
async function modelCatalogChecks(page, fixture) {
  fixture.data = workspace();
  for (const available of [true, false]) {
    fixture.data.orchestrator.account.models = available ? workspace().orchestrator.account.models : [];
    await page.goto(origin + '/#documents');
    await page.reload();
    await page.click('#toggle-assistant');
    await page.click('#composer-agents summary');
    assert.equal(await page.locator('.agent-menu-row').count(), 1);
    assert.equal(await page.locator('.agent-menu-row h3').textContent(), 'ApplyDesk Agent');
    for (const role of Object.keys(fixture.data.orchestrator.settings.roles)) {
      const select = page.locator(`[name="${role}.model"]`);
      assert.equal(await select.inputValue(), available ? 'test-model' : '');
      assert.deepEqual(await select.locator('option').allTextContents(),
        available ? ['GPT-6.1-Sol'] : ['test-model (unavailable)']);
    }
  }
}

// Archived letters remain readable without controls that generate or edit them.
async function readOnlyDocumentChecks(page, fixture) {
  const before=fixture.writes.length;
  await page.evaluate(() => import('/editor.js').then(module=>module.openEditor('old-cover')));
  await page.getByTitle('Saved document preview').waitFor();
  assert.equal(await page.locator('#save-document,#agent-toggle,[data-block],#ask-document-agent').count(),0);
  assert.equal(await page.getByRole('link',{name:'Download PDF'}).count(),1);
  assert.equal(fixture.writes.length,before);
  await page.locator('#modal .close').click();
}

// Watching the active application restores its window without sending another agent message.
async function applicationWindowChecks(page, fixture) {
  fixture.data = workspace();
  fixture.data.orchestrator.application_browser = {available: false, job_id: null};
  await page.goto(origin + '/#applications');
  await page.reload();
  await page.click('#toggle-assistant');
  assert.equal(await page.locator('#show-application').isVisible(), false);
  fixture.data.orchestrator.application_browser = {available: true, job_id: 'job'};
  await page.evaluate(() => import('/ui.js').then(module => module.refresh()));
  await page.getByRole('button', {name: 'Show the application workspace beside ApplyDesk'}).waitFor();
  const before = fixture.writes.length;
  await page.click('#show-application');
  await page.waitForFunction(() => document.querySelector('#show-application').disabled === false);
  const writes=fixture.writes.slice(before);
  assert.equal(writes.length,1);
  assert.equal(writes[0].path,'/api/orchestrator/show-application');
  assert.deepEqual(Object.keys(writes[0].data),['bounds']);
  assert.ok(Object.values(writes[0].data.bounds).every(Number.isSafeInteger));
  fixture.data.orchestrator.application_browser = {available: false, job_id: null};
  await page.evaluate(() => import('/ui.js').then(module => module.refresh()));
  assert.equal(await page.locator('#show-application').isVisible(), false);
}

// Close the isolated Chrome profile even when a browser assertion fails.
async function main() {
  const fixture = {data: workspace(), writes: [], failDelete: false}, errors = [];
  const browser = await chromium.launch({channel: 'chrome', headless: true, chromiumSandbox: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 950}});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => routeRequest(route, fixture));
    await deletionChecks(page, fixture);
    await searchChecks(page, fixture);
    await layoutChecks(page, fixture);
    await modelCatalogChecks(page, fixture);
    await applicationWindowChecks(page, fixture);
    await readOnlyDocumentChecks(page, fixture);
    await requiredLetterChecks(page, fixture);
    assert.deepEqual(errors, []);
    console.log('Document deletion, public Google search, removed platform controls, model availability and responsive layouts passed.');
  } catch(error) {
    if(errors.length)error.message+='\nBrowser errors: '+errors.join('; ');
    throw error;
  } finally { await browser.close(); }
}

// A required letter shows the existing Needs input status and its reason in details.
async function requiredLetterChecks(page, fixture) {
  fixture.data = workspace();
  const issue='This application requires a cover letter. Continue it manually; letter generation is unavailable.';
  Object.assign(fixture.data.jobs[0],{status:'needs_review',cover_state:'required',exceptions:[issue]});
  await page.goto(origin + '/#applications');
  await page.reload();
  await page.locator('[data-filter="needs_review"]').click();
  assert.equal(await page.locator('[data-job-status="job"] option:checked').textContent(),'Needs input');
  await page.click('[data-detail="job"]');
  assert.equal(await page.locator('#modal [data-job-status="job"] option:checked').textContent(),'Needs input');
  assert.ok((await page.locator('#modal').textContent()).includes(issue));
}

module.exports={workspace,routeRequest};
if(require.main===module)main().catch(error => { console.error(error); process.exitCode = 1; });

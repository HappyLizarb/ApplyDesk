const assert = require('node:assert/strict');
const http = require('node:http');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const {installGuards, installOwnedPageGuards, disposeOwnedPageGuards} = require('../browser/guard.cjs');
const {inspect} = require('../browser/inspect.cjs');
const {armAshbyAnswer} = require('../browser/ashby-drafts.cjs');
const {pauseWorkspace} = require('../browser/control.cjs');
const queries = require('./fixtures/ashby-draft-queries.json');

const html = `<!doctype html><title>Example application</title><main>
<label>First name<input name="first"></label>
<button type="button" id="draft" onclick="fetch('/draft',{method:'POST'})">Save draft</button>
<button type="button" id="final" onclick="fetch('/final',{method:'POST'})">Submit application</button>
<form id="ordinary" method="post" action="/step"><label>City<input name="city"></label><button>Save and Continue</button><button hidden>Submit application</button></form>
<form id="finalform" method="post" action="/finalform"><button>Submit application</button></form>
<script>fetch('/initialize',{method:'POST'});</script></main>`;

// A synthetic employer records ordinary posts independently of the controller's state.
async function fixture() {
  const received = [], pending = new Map();
  const server = http.createServer((request, response) => {
    if (request.method === 'POST') received.push(request.url);
    if(request.url.startsWith('/pending-')){pending.set(request.url,response);return;}
    response.setHeader('Content-Type', request.method === 'POST' && request.url !== '/step' ? 'application/json' : 'text/html');
    response.end(request.method === 'POST' && request.url !== '/step' ? '{}' : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, received, pending, url: `http://127.0.0.1:${server.address().port}`};
}

// Existing tabs get no hooks; owned tabs preserve identity and report their actual boundary.
async function testOwnedScope(context, server) {
  const personal = await context.newPage();await personal.goto(server.url);
  const page = await context.newPage();
  const state = {normalNetworking: true, manual: false, paused: false, allowLocal: true, blocked: [], unexpectedPages: new Set()};
  await assert.rejects(installGuards(context, state), /owned page only/);
  await installOwnedPageGuards(page, state);
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/initialize')), page.goto(server.url)]);
  assert.equal(await personal.evaluate(() => typeof globalThis.__applyDesk), 'undefined');
  const first = await inspect(page, state), second = await inspect(page, state);
  assert.equal(first.fields[0].field_id, second.fields[0].field_id);
  assert.deepEqual(first.guard, {manual: false, mutations_blocked: false, submission_guarded: false,
    page_networking: 'normal', action_audited: true, blocked_requests: [], blocked_telemetry_count: 0});
  assert.deepEqual(first.warnings, []);
  state.unexpectedPages.add(personal);
  assert((await inspect(page, state)).warnings.some(message => /additional browser tab/.test(message)));
  state.unexpectedPages.clear();
  return {page, state, personal};
}

// Ordinary background traffic keeps working while explicit final buttons require their exact permit.
async function testNormalRequests(page, state, server) {
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/draft')), page.locator('#draft').click()]);
  const before = server.received.length;
  await page.locator('#final').click();
  await page.locator('#finalform').evaluate(form => form.requestSubmit());
  await page.waitForTimeout(50);assert.equal(server.received.length, before);
  state.paused = true;
  await page.evaluate(() => fetch('/background-autosave', {method: 'POST'}));
  assert(server.received.includes('/background-autosave'));
  state.paused = false;
  const snapshot = await inspect(page, state), final = snapshot.buttons.find(button => button.text === 'Submit application');
  await page.evaluate(id => Object.assign(globalThis.__applyDesk, {finalId: id, finalUntil: Date.now() + 5000}), final.field_id);
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/final')), page.locator('#final').click()]);
  assert(server.received.includes('/final'));assert(!server.received.includes('/finalform'));
  await page.evaluate(() => {globalThis.__applyDesk.finalUntil = 0;});
  await page.locator('input[name="city"]').fill('Example City');
  await Promise.all([page.waitForURL(server.url + '/step'), page.locator('input[name="city"]').press('Enter')]);
  assert(server.received.includes('/step'));assert.deepEqual(state.blocked, []);
}

// Parking cancels agent actions without manufacturing failed reads or interrupting native autosaves.
async function testParkPendingRequests(context,page,state,server) {
  state.controller=new AbortController();
  const signal=state.controller.signal;
  await page.evaluate(()=>{
    globalThis.fixtureReads={};
    for(const [name,method] of [['terms','GET'],['save','POST']])fetch('/pending-'+name,{method})
      .then(r=>r.text()).then(()=>globalThis.fixtureReads[name]='complete').catch(()=>globalThis.fixtureReads[name]='VPS');
  });
  const deadline=Date.now()+2000;
  while(server.pending.size<2&&Date.now()<deadline)await page.waitForTimeout(20);
  assert.equal(server.pending.size,2);
  await pauseWorkspace({context,tabs:new Map([['job',{jobId:'job',page,state}]])});
  assert(signal.aborted);assert.equal(state.paused,true);
  assert.equal(await page.evaluate(()=>globalThis.__applyDesk.paused),true);
  for(const response of server.pending.values())response.end('{}');
  await page.waitForFunction(()=>Object.values(globalThis.fixtureReads).filter(value=>value==='complete').length===2);
  assert.deepEqual(await page.evaluate(()=>globalThis.fixtureReads),{terms:'complete',save:'complete'});
  const requests=await page.requests();
  assert(requests.filter(r=>r.url().includes('/pending-')).every(r=>!r.failure()));
}

// Releasing an owned tab removes future-document hooks and restores current-document use.
async function testDisconnect(page, state, personal, server) {
  await disposeOwnedPageGuards(page, state);
  assert.equal(state.guardInstalled, false);
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/final')), page.locator('#final').click()]);
  await page.goto(server.url);
  assert.equal(await page.evaluate(() => typeof globalThis.__applyDesk), 'undefined');
  assert.equal(await personal.evaluate(() => typeof globalThis.__applyDesk), 'undefined');
}

// Capture the installed page route without exposing a production bypass or touching a browser.
async function ownedRoute(state) {
  let handler;
  const page = {addInitScript: async () => ({dispose: async () => {}}), evaluate: async () => {},
    route: async (_, callback) => {handler = callback;}, on: () => {}};
  await installOwnedPageGuards(page, state);
  return async request => {
    let override;
    await handler({request: () => request, continue: async value => {override = value;}, abort: () => assert.fail('Normal employer request was blocked')});
    return override;
  };
}

// Normal traffic still passes through the exact-byte override needed for Chrome multipart uploads.
async function testVerifiedUpload() {
  const pageUrl = 'https://example.wd5.myworkdayjobs.com/en-US/Site/job/US/Engineer_JR1/apply/applyManually';
  const uploadUrl = 'https://example.wd5.myworkdayjobs.com/wday/calypso/cxs/common/example/attachments';
  const bytes = Buffer.from('%PDF-1.4 synthetic unchanged resume');
  const form = new FormData();form.append('body', new Blob([], {type: 'application/pdf'}), 'resume.pdf');
  const encoded = new Request(uploadUrl, {method: 'POST', body: form}), body = Buffer.from(await encoded.arrayBuffer());
  const frame = {url: () => pageUrl, page: () => ({mainFrame: () => frame})};
  const state = {normalNetworking: true, allowLocal: true, paused: false, generation: 2, blocked: [],
    workdayUpload: {pageUrl, uploadUrl, filename: 'resume.pdf', size: bytes.length, bytes,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'), generation: 2, expires: Date.now() + 30000}};
  const request = {url: () => uploadUrl, method: () => 'POST', frame: () => frame,
    headers: () => ({'content-type': encoded.headers.get('content-type')}), postDataBuffer: () => body};
  const route = await ownedRoute(state), override = await route(request);
  assert.equal(state.workdayUpload.request, request);
  const forwarded = await new Response(override.postData, {headers: override.headers}).formData();
  assert.deepEqual(Buffer.from(await forwarded.get('body').arrayBuffer()), bytes);
}

// Draft and submission acknowledgements keep their exact request identities without blocking other writes.
async function testAshbyObservers() {
  const pageUrl = 'https://jobs.ashbyhq.com/example/job/application', endpoint = 'https://jobs.ashbyhq.com/api/non-user-graphql';
  const frame = {url: () => pageUrl, page: () => ({mainFrame: () => frame})};
  const request = body => ({url: () => endpoint, method: () => 'POST', frame: () => frame, postDataJSON: () => body, postData: () => JSON.stringify(body)});
  const state = {normalNetworking: true, allowLocal: true, paused: false, blocked: [], ashbyEdits: new Map(),
    ashbyForms: new Map([['form', {definition: 'definition', pageUrl, fields: new Map([['name', {type: 'String'}]])}]])};
  const route = await ownedRoute(state), edit = armAshbyAnswer(state, {ashby_binding: {form: 'form', path: 'name'}}, 'Example Person');
  const values = {organizationHostedJobsPageName: 'example', formRenderIdentifier: 'form', path: 'name', value: 'Example Person', formDefinitionIdentifier: 'definition'};
  const save = request({operationName: 'ApiSetFormValue', query: queries.ApiSetFormValue, variables: values});
  await route(save);assert.equal(edit.request, save);
  await route(request({operationName: 'ApiSetFormValue', query: queries.ApiSetFormValue, variables: {...values, value: 'Background value'}}));
  assert.equal(edit.request, save);
  const binding = {pageUrl, tenant: 'example', job: 'job', form: 'form', action: 'submit', definition: 'definition', surveys: []};
  const submitted = {organizationHostedJobsPageName: 'example', jobPostingId: 'job', applicationFormRenderIdentifier: 'form',
    applicationFormActionIdentifier: 'submit', applicationFormDefinitionIdentifier: 'definition', surveyIdentifiers: [], recaptchaToken: 'fixture-token'};
  state.submission = {origin: 'https://jobs.ashbyhq.com', url: endpoint, expires: Date.now() + 30000, used: false, ashby: binding};
  const final = request({operationName: 'ApiSubmitMultipleFormsAction', query: queries.ApiSubmitMultipleFormsAction, variables: submitted});
  await route(final);assert.equal(state.submission.request, final);assert.equal(state.submission.used, true);
}

// Existing Chrome extensions and browser-managed resources keep Chrome's own network policy.
async function testChromeResources() {
  const state = {normalNetworking: true, paused: false, blocked: []}, route = await ownedRoute(state);
  for (const url of ['chrome-extension://fixture-id/web_accessible_resources/redirects/google-analytics_analytics.js',
    'blob:https://employer.example/fixture', 'data:text/plain,fixture']) {
    assert.equal(await route({url: () => url, method: () => 'GET'}), undefined);
  }
  assert.deepEqual(state.blocked, []);
}

// No personal Chrome profile, live employer, credentials or real application is used.
async function main() {
  await testVerifiedUpload();await testAshbyObservers();await testChromeResources();
  const server = await fixture(), browser = await chromium.launch({headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext();
  try {
    const {page, state, personal} = await testOwnedScope(context, server);
    await testNormalRequests(page, state, server);
    await testParkPendingRequests(context,page,state,server);
    await testDisconnect(page, state, personal, server);
    console.log('Owned normal browsing permits initialization, autosaves and native steps; final gating, file bytes, metadata and disconnect isolation passed.');
  } finally {
    await browser.close();await new Promise(resolve => server.server.close(resolve));
  }
}
main().catch(error => {console.error(error);process.exitCode = 1;});

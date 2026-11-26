const assert = require('node:assert/strict');
const http = require('node:http');
const {chromium} = require('playwright');
const {installOwnedPageGuards} = require('../browser/guard.cjs');
const {agentObserve} = require('../browser/agent-page.cjs');
const {agentSubmit} = require('../browser/agent-submission.cjs');
const {revoke} = require('../browser/control.cjs');

// A previously unseen synthetic employer renders only summary text and a plain Submit control.
async function fixtureServer() {
  const posts = [];
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    if (request.method === 'POST') {
      posts.push(request.url);
      return response.end(request.url === '/pending' ? '<h1>Processing your request</h1>' : '<h1>Your application has been received</h1>');
    }
    const declaration = request.url === '/declaration' ? '<p>By submitting you certify that you hold an active security clearance.</p>' : '<p>By submitting you certify the information is accurate.</p>';
    const password = request.url === '/authentication' ? '<label>Password<input type="password"></label>' : '';
    const action = request.url === '/uncertain' || request.url === '/cancel' ? '/pending' : '/complete';
    response.end(`<main><h1>Example Co — Hardware Graduate</h1><form method="post" action="${action}">
      <p>Name: Alex Test</p><p>Resume: resume.pdf</p>${declaration}${password}<button>Submit</button></form>
      <footer><a href="/terms">Terms of Service</a><a href="/signin">Sign In</a><p>Privacy notice</p></footer>
      <form><input aria-label="Search jobs" value="hardware"><input aria-label="Newsletter email" required type="email"></form>
      <div role="alert">Upload successful</div></main>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, posts, origin: `http://127.0.0.1:${server.address().port}`};
}

// Permission is bound to the observation after the Python review and durable reservation.
function permission(observed) {
  return {snapshot_id: observed.snapshot_id, final_element_id: observed.elements.find(item => item.label === 'Submit').id,
    company: 'Example Co', title: 'Hardware Graduate', company_quote: 'Example Co', title_quote: 'Hardware Graduate',
    resume_quote: 'Resume: resume.pdf', attempt_id: 'attempt-1', review_digest: 'a'.repeat(64)};
}

async function ownedPage(browser, url) {
  const page = await browser.newPage();
  const state = {workspace: true, normalNetworking: true, manual: false, paused: false, generation: 0,
    blocked: [], allowLocal: true, controller: new AbortController()};
  await installOwnedPageGuards(page, state);
  await page.goto(url);
  return {page, state};
}

// A real POST and newly visible receipt prove completion; ordinary clicks remain gated.
async function testReceipt(browser, fixture) {
  const {page, state} = await ownedPage(browser, fixture.origin);
  try {
    const count = fixture.posts.length;
    await page.locator('button').click();
    assert.equal(fixture.posts.length, count);
    const before = await agentObserve(page, state);
    const form = before.elements.find(item => item.label === 'Submit').form_id;
    assert.equal(before.elements.filter(item => item.tag === 'input' && item.form_id === form).length, 0);
    const result = await agentSubmit(page, state, permission(before));
    assert.equal(result.confirmed, true);
    assert.equal(result.attempt_id, 'attempt-1');
    assert.match(result.receipt, /application has been received/);
    assert.equal(fixture.posts.length, count + 1);
    await page.goto(fixture.origin);
    await assert.rejects(agentSubmit(page, state, permission(await agentObserve(page, state))), /already attempted/);
    assert.equal(fixture.posts.length, count + 1);
  } finally { await page.close(); }
}

// Changed content, disabled controls, authentication and declarations cannot consume a click.
async function testRejections(browser, fixture) {
  const {page, state} = await ownedPage(browser, fixture.origin);
  const count = fixture.posts.length;
  try {
    const before = await agentObserve(page, state);
    await page.locator('p').first().evaluate(item => {item.textContent = 'Changed answer';});
    await assert.rejects(agentSubmit(page, state, permission(before)), /changed after review/);
    await page.locator('button').evaluate(item => {item.disabled = true;});
    await assert.rejects(agentSubmit(page, state, permission(await agentObserve(page, state))), /unavailable/);
    await page.goto(fixture.origin + '/authentication');
    await assert.rejects(agentSubmit(page, state, permission(await agentObserve(page, state))), /Authentication/);
    await page.goto(fixture.origin + '/declaration');
    await assert.rejects(agentSubmit(page, state, permission(await agentObserve(page, state))), /needs review/);
    assert.equal(state.submissionAttempt, undefined);
    assert.equal(fixture.posts.length, count);
  } finally { await page.close(); }
}

// Lack of a receipt remains uncertain, and the browser prevents a second send.
async function testUncertain(browser, fixture) {
  const {page, state} = await ownedPage(browser, fixture.origin + '/uncertain');
  try {
    const count = fixture.posts.length;
    const result = await agentSubmit(page, state, permission(await agentObserve(page, state)));
    assert.equal(result.confirmed, false);
    assert.equal(result.receipt, '');
    assert.equal(fixture.posts.length, count + 1);
    await page.goto(fixture.origin + '/uncertain');
    await assert.rejects(agentSubmit(page, state, permission(await agentObserve(page, state))), /already attempted/);
    assert.equal(fixture.posts.length, count + 1);
  } finally { await page.close(); }
}

// Stop ends receipt observation promptly without making the already-sent request retryable.
async function testCancellation(browser, fixture) {
  const {page, state} = await ownedPage(browser, fixture.origin + '/cancel');
  try {
    const request = page.waitForRequest(request => request.method() === 'POST');
    const submitted = agentSubmit(page, state, permission(await agentObserve(page, state)));
    await request;
    revoke(state);
    await assert.rejects(submitted, /paused/);
    assert.equal(state.submissionAttempt, 'attempt-1');
  } finally { await page.close(); }
}

async function main() {
  const fixture = await fixtureServer();
  const browser = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    await testReceipt(browser, fixture);
    await testRejections(browser, fixture);
    await testUncertain(browser, fixture);
    await testCancellation(browser, fixture);
    console.log('Generic review submission passed: read-only summary, plain Submit, one real POST/receipt, stale/auth/declaration rejection, uncertainty and Stop.');
  } finally { await browser.close(); await new Promise(resolve => fixture.server.close(resolve)); }
}
main().catch(error => { console.error(error);process.exitCode = 1; });

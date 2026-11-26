const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// The fixture counts received submissions; no employer site or personal data is involved.
async function fixtureServer() {
  const posts = [];
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    if (request.method === 'POST') {
      posts.push(request.url);
      return response.end(request.url === '/uncertain' ? '<h1>Processing</h1>' : '<h1>Your application has been submitted</h1>');
    }
    const target = request.url === '/no-receipt' ? '/uncertain' : '/submit';
    const terms = {
      '/terms': '<p>By submitting you agree to our terms of service.</p>',
      '/declaration': '<p>By submitting, I consent to optional interview recording.</p>',
    }[request.url] || '';
    const form = `<main><h1>Example Co — Analyst</h1><form method="post" action="${target}"><label>Full name<input name="name"></label><label>Resume<input name="resume" type="file"></label>${terms}<button>Submit application</button></form></main>`;
    if (request.url === '/platform') return response.end(`<main><h1>Example Co — Analyst</h1><button onclick="document.querySelector('dialog').showModal()">Apply</button><dialog>${form}</dialog></main>`);
    if (request.url === '/deceptive-entry') return response.end('<main><h1>Example Co — Analyst</h1><button onclick="fetch(\'/submit\',{method:\'POST\'})">Apply</button></main>');
    if (request.url === '/external-entry') return response.end('<main><h1>Example Co — Analyst</h1><button onclick="window.open(\'/employer\')">Apply externally</button><button onclick="window.open(\'/employer\')">Apply externally</button></main>');
    response.end(form);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, posts, origin: `http://127.0.0.1:${server.address().port}`};
}

// Only the exact current submit control can be invoked, once per owned session.
function permission(snapshot) {
  return {snapshot_id: snapshot.snapshot_id, field_id: snapshot.buttons.find(button => button.text === 'Submit application').field_id,
    company: 'Example Co', title: 'Analyst', attempt_id: 'attempt-1'};
}

async function testConfirmed(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'confirmed'), headless: true, allowLocal: true});
  try {
    let snapshot = await dispatch(session, 'open', {url: fixture.origin + '/terms'});
    const before = fixture.posts.length;
    await session.page.locator('button').click();
    assert.equal(fixture.posts.length, before, 'ordinary clicks cannot submit');
    snapshot = await dispatch(session, 'snapshot', {});
    const result = await dispatch(session, 'submit', permission(snapshot));
    assert.equal(result.confirmed, true);
    assert.match(result.receipt, /application has been submitted/);
    assert.equal(fixture.posts.length, before + 1);
    snapshot = await dispatch(session, 'open', {url: fixture.origin});
    await assert.rejects(dispatch(session, 'submit', permission(snapshot)), /already attempted/);
  } finally { await session.context.close(); }
}

async function testChangedAndDeclared(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'blocked'), headless: true, allowLocal: true});
  try {
    const before = fixture.posts.length;
    let snapshot = await dispatch(session, 'open', {url: fixture.origin});
    await session.page.locator('input[name=name]').fill('Changed after audit');
    await assert.rejects(dispatch(session, 'submit', permission(snapshot)), /form changed/);
    snapshot = await dispatch(session, 'open', {url: fixture.origin + '/declaration'});
    await assert.rejects(dispatch(session, 'submit', permission(snapshot)), /manual review/);
    assert.equal(fixture.posts.length, before);
  } finally { await session.context.close(); }
}

async function testUncertain(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'uncertain'), headless: true, allowLocal: true});
  try {
    const before = fixture.posts.length;
    const snapshot = await dispatch(session, 'open', {url: fixture.origin + '/no-receipt'});
    const result = await dispatch(session, 'submit', permission(snapshot));
    assert.equal(result.confirmed, false);
    assert.equal(result.receipt, '');
    assert.equal(fixture.posts.length, before + 1);
  } finally { await session.context.close(); }
}

async function testPlatformEntry(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'platform'), applicationSource: 'handshake', headless: true, allowLocal: true});
  try {
    let snapshot = await dispatch(session, 'open', {url: fixture.origin + '/platform'});
    assert.equal(snapshot.fields.length, 0);
    snapshot = await dispatch(session, 'application_entry', {snapshot_id: snapshot.snapshot_id, company: 'Example Co', title: 'Analyst'});
    assert.equal(snapshot.fields.length, 2);
    assert.equal(snapshot.buttons.length, 1);
    const name = snapshot.fields.find(field => field.name === 'name');
    snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: name.field_id, value: 'Alex Test'}]});
    assert.equal(snapshot.fields.find(field => field.name === 'name').value, 'Alex Test');
    assert.equal((await dispatch(session, 'submit', permission(snapshot))).confirmed, true);
  } finally { await session.context.close(); }
}

async function testDeceptiveEntry(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'deceptive'), applicationSource: 'handshake', headless: true, allowLocal: true});
  try {
    const before = fixture.posts.length;
    const snapshot = await dispatch(session, 'open', {url: fixture.origin + '/deceptive-entry'});
    await assert.rejects(dispatch(session, 'application_entry', {snapshot_id: snapshot.snapshot_id, company: 'Example Co', title: 'Analyst'}), /unverified write/);
    assert.equal(fixture.posts.length, before);
  } finally { await session.context.close(); }
}

async function testExternalEntry(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'external'), applicationSource: 'handshake', headless: true, allowLocal: true});
  try {
    const before = fixture.posts.length;
    const snapshot = await dispatch(session, 'open', {url: fixture.origin + '/external-entry'});
    const result = await dispatch(session, 'application_entry', {snapshot_id: snapshot.snapshot_id, company: 'Example Co', title: 'Analyst'});
    assert.equal(result.external_application_url, fixture.origin + '/employer');
    assert.equal(fixture.posts.length, before);
  } finally { await session.context.close(); }
}

// Temporary profiles and the local server are always removed, including after a failed assertion.
async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-submit-test-'));
  const fixture = await fixtureServer();
  try {
    await testConfirmed(directory, fixture);
    await testChangedAndDeclared(directory, fixture);
    await testUncertain(directory, fixture);
    await testPlatformEntry(directory, fixture);
    await testDeceptiveEntry(directory, fixture);
    await testExternalEntry(directory, fixture);
    console.log('Submission browser checks passed: real POST, receipt, stale form, declarations, uncertain result, modal entry, deceptive Apply.');
  } finally { await new Promise(resolve => fixture.server.close(resolve));await fs.rm(directory, {recursive: true, force: true}); }
}

main().catch(error => { console.error(error);process.exitCode = 1; });

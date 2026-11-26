const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {start, dispatch} = require('../browser/worker.cjs');
const {pauseWorkspace} = require('../browser/control.cjs');
const {sourceStorage} = require('../browser/source-storage.cjs');
const {fixtureServer} = require('./recommendations-fixtures.cjs');

// Every form and network mutation is fictional and hosted by this temporary local server.
async function fixture() {
  const writes = [], visits = [];
  const server = http.createServer((request, response) => {
    visits.push(request.url);
    if (request.method === 'POST') writes.push(request.url);
    response.end('<title>Application workspace test</title><h1>Example job</h1>' +
      '<form method="POST" action="/submit"><label>Name<input name="name"></label>' +
      '<div style="height:1000px"></div><label>Email<input name="email" value="old@example.test"></label>' +
      '<button type="button" onclick="this.textContent=\'Advanced\'">Next</button></form>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, writes, visits, url: `http://127.0.0.1:${server.address().port}`};
}

// Jobs keep separate tabs and retain their own answers as the selected job changes.
async function tabChecks(session, site) {
  const first = await dispatch(session, 'open', {job_id: 'first', url: site.url + '/one'});
  const page = session.page;
  await dispatch(session, 'fill', {job_id: 'first', snapshot_id: first.snapshot_id, answers: [
    {field_id: first.fields.find(field => field.name === 'name').field_id, value: 'Example Person'},
    {field_id: first.fields.find(field => field.name === 'email').field_id, value: ''}]});
  assert.equal(await page.locator('[name=name]').inputValue(), 'Example Person');
  assert.equal(await page.locator('[name=email]').inputValue(), '');
  await dispatch(session, 'open', {job_id: 'second', url: site.url + '/two'});
  assert(!(await dispatch(session, 'snapshot', {job_id: 'second'})).warnings.some(text => /additional browser tab/.test(text)));
  const unexpected = await session.context.newPage();
  assert((await dispatch(session, 'snapshot', {job_id: 'second'})).warnings.some(text => /additional browser tab/.test(text)));
  await unexpected.close();
  const second = session.page;
  assert.notEqual(second, page);
  assert.equal(session.context.pages().length, 2);
  assert.equal(await page.locator('[name=name]').inputValue(), 'Example Person');
  await dispatch(session, 'open', {job_id: 'second', url: site.url + '/two-again'});
  assert.equal(session.page, second);
  assert.equal(session.context.pages().length, 2);
}

// Stop during the first field prevents the second write while preserving the displayed form.
async function pauseChecks(session, site) {
  const before = await dispatch(session, 'snapshot', {job_id: 'second'}), page = session.page;
  // Leave time to pause the isolated tab between the two field writes.
  session.tabs.get('second').state.pace = true;
  const filling = dispatch(session, 'fill', {job_id: 'second', snapshot_id: before.snapshot_id, answers: [
    {field_id: before.fields.find(field => field.name === 'name').field_id, value: 'Visible first change'},
    {field_id: before.fields.find(field => field.name === 'email').field_id, value: 'must-not-write@example.test'}]});
  const rejected = assert.rejects(filling, /paused/);
  await page.waitForFunction(() => document.querySelector('[name=name]').value === 'Visible first change');
  await pauseWorkspace(session);
  await rejected;
  assert.equal(await page.locator('[name=email]').inputValue(), 'old@example.test');
  assert.equal(page.isClosed(), false);
  await assert.rejects(dispatch(session, 'next', {job_id: 'second', snapshot_id: before.snapshot_id}), /paused/);
  await page.evaluate(() => fetch('/forbidden', {method: 'POST'}).catch(() => {}));
  assert.deepEqual(site.writes, []);
  const resumed = await dispatch(session, 'open', {job_id: 'second', url: site.url + '/resumed'});
  assert.equal(session.page, page);
  await dispatch(session, 'fill', {job_id: 'second', snapshot_id: resumed.snapshot_id,
    answers: [{field_id: resumed.fields.find(field => field.name === 'name').field_id, value: 'Explicitly resumed'}]});
  assert.equal(await page.locator('[name=name]').inputValue(), 'Explicitly resumed');
}

// Stop cancels an already waiting driver action, not just the next field in a batch.
async function pendingActionChecks(session, site) {
  const before = await dispatch(session, 'open', {job_id: 'second', url: site.url + '/waiting'}), page = session.page;
  await page.evaluate(() => {
    const input = document.querySelector('[name=name]');input.value = 'initial';
  });
  const snapshot = await dispatch(session, 'snapshot', {job_id: 'second'});
  await page.evaluate(() => {
    const input = document.querySelector('[name=name]');
    setTimeout(() => { input.disabled = true; }, 80);
    setTimeout(() => { input.disabled = false; }, 1400);
  });
  const filling = dispatch(session, 'fill', {job_id: 'second', snapshot_id: snapshot.snapshot_id,
    answers: [{field_id: before.fields.find(field => field.name === 'name').field_id, value: 'must-not-write'}]});
  const rejected = assert.rejects(filling, /paused|abort/i);
  await page.waitForTimeout(500);await pauseWorkspace(session);await rejected;
  await page.waitForTimeout(1100);
  assert.equal(await page.locator('[name=name]').inputValue(), 'initial');
  assert.equal(page.isClosed(), false);
  await dispatch(session, 'open', {job_id: 'second', url: site.url + '/after-wait'});
  assert.equal(session.page, page);
}

// Manual control in one tab never authorizes requests from a different retained job.
async function isolationChecks(session, site) {
  const first = session.tabs.get('first').page, second = session.tabs.get('second').page;
  await dispatch(session, 'focus', {job_id: 'second', manual: true});
  await first.evaluate(() => fetch('/inactive', {method: 'POST'}).catch(() => {}));
  await second.evaluate(() => fetch('/manual', {method: 'POST'}).catch(() => {}));
  assert.deepEqual(site.writes, ['/manual']);
  await pauseWorkspace(session);
  await second.evaluate(() => fetch('/stopped-manual', {method: 'POST'}).catch(() => {}));
  assert.deepEqual(site.writes, ['/manual']);
  await first.close();
  assert.equal(session.page, second);
  await assert.rejects(dispatch(session, 'fill', {job_id: 'first', answers: []}), /tab is closed/);
}

// A socket opened during manual control cannot forward messages after Stop revokes the tab.
async function socketChecks(session) {
  const fixture = await fixtureServer(), connections = new Set();
  fixture.server.on('connection', socket => connections.add(socket));
  try {
    await dispatch(session, 'open', {job_id: 'second', url: fixture.origin});
    await dispatch(session, 'focus', {job_id: 'second', manual: true});
    const page = session.page;
    await page.evaluate(url => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Socket fixture did not echo.')), 5000);
      window.socket = new WebSocket(url);socket.onopen = () => socket.send('before-stop');
      socket.onmessage = () => { clearTimeout(timer);resolve(); };
      socket.onerror = () => { clearTimeout(timer);reject(new Error('Socket fixture could not connect.')); };
    }), fixture.origin.replace('http:', 'ws:'));
    assert.deepEqual(fixture.websocket.messages, ['before-stop']);
    await pauseWorkspace(session);
    await page.evaluate(() => { if (socket.readyState === WebSocket.OPEN) socket.send('after-stop'); });
    await page.waitForFunction(() => socket.readyState === WebSocket.CLOSED);
    assert.deepEqual(fixture.websocket.messages, ['before-stop']);
    assert.equal(session.tabs.get('second').state.sockets.size, 0);
  } finally {
    await session.page.goto('about:blank');
    for (const socket of connections) socket.destroy();
    fixture.server.closeAllConnections();
    await new Promise(resolve => fixture.server.close(resolve));
  }
}

// Session transfer rejects unrelated domains and never overwrites a refreshed live storage key.
async function storageChecks(session) {
  const cookie = {name: 'fixture-session', value: 'fictional-secret', domain: '.linkedin.com', path: '/', secure: true, httpOnly: true};
  const storage = {cookies: [cookie], origins: [{origin: 'https://www.linkedin.com', localStorage: [
    {name: 'fixture-token', value: 'initial'}, {name: 'old-account-key', value: 'remove-on-reconnect'}]}]};
  const mixed = {cookies: [...storage.cookies, {...cookie, domain: '.unrelated.test'}], origins: [...storage.origins, {origin: 'https://unrelated.test', localStorage: []}]};
  const exported = await sourceStorage({state: {source: 'linkedin'}, context: {storageState: async () => mixed}});
  assert.deepEqual(exported, storage);
  await assert.rejects(dispatch(session, 'import_source', {source: 'linkedin', storage: mixed}), error =>
    !error.message.includes('fictional-secret') && error.message.includes('Could not reuse'));
  await dispatch(session, 'import_source', {source: 'linkedin', storage});
  const page = session.page;
  await page.route('https://www.linkedin.com/**', route => route.fulfill({body: '<title>Fictional platform page</title>'}));
  await dispatch(session, 'open', {job_id: 'second', url: 'https://www.linkedin.com/test'});
  assert.equal(await page.evaluate(() => localStorage.getItem('fixture-token')), 'initial');
  await page.evaluate(() => localStorage.setItem('fixture-token', 'refreshed'));
  await page.reload();
  assert.equal(await page.evaluate(() => localStorage.getItem('fixture-token')), 'refreshed');
  await page.evaluate(() => localStorage.setItem('workspace-draft', 'keep'));
  let extraPages = 0;const opened = () => { extraPages += 1; };session.context.on('page', opened);
  await dispatch(session, 'import_source', {source: 'linkedin', storage: {cookies: [{...cookie, value: 'new-account'}],
    origins: [{origin: 'https://www.linkedin.com', localStorage: [{name: 'fixture-token', value: 'new-account'}]}]}});
  session.context.removeListener('page', opened);assert.equal(extraPages, 0);
  assert.deepEqual(await page.evaluate(() => [localStorage.getItem('fixture-token'), localStorage.getItem('old-account-key'),
    localStorage.getItem('workspace-draft')]), ['new-account', null, 'keep']);
  assert.equal((await session.context.cookies('https://unrelated.test')).length, 0);
  await dispatch(session, 'close_job', {job_id: 'second'});
  assert.equal(session.tabs.size, 0);
  assert.equal(session.context.pages().length, 1);
  assert.equal(session.context.pages()[0].url(), 'about:blank');
}

// Imported key ownership survives restart without storing any sign-in values in metadata.
async function storageRestartChecks(session) {
  await session.context.close();
  const metadata = await fs.readFile(path.join(session.options.dataRoot, 'imported-storage-keys.json'), 'utf8');
  assert.equal(metadata.includes('new-account'), false);assert.equal(metadata.includes('fictional-secret'), false);
  const reopened = await start(session.options);
  try {
    await dispatch(reopened, 'import_source', {source: 'linkedin', storage: {cookies: [], origins: []}});
    await reopened.page.route('https://www.linkedin.com/**', route => route.fulfill({body: '<title>Restart fixture</title>'}));
    await dispatch(reopened, 'open', {job_id: 'restart', url: 'https://www.linkedin.com/test'});
    assert.deepEqual(await reopened.page.evaluate(() => [localStorage.getItem('fixture-token'),
      localStorage.getItem('workspace-draft')]), [null, 'keep']);
    assert.deepEqual(await reopened.context.cookies('https://www.linkedin.com'), []);
  } finally { await reopened.context.close(); }
}

// A disposable headless profile keeps the fixture independent of desktop Chrome.
async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-workspace-')), site = await fixture();
  let session;
  try {
    session = await start({dataRoot: root, applicationWorkspace: true, headless: true, allowLocal: true});
    await tabChecks(session, site);await pauseChecks(session, site);await pendingActionChecks(session, site);
    await isolationChecks(session, site);await socketChecks(session);await storageChecks(session);await storageRestartChecks(session);
    console.log('Headless application workspace passed retained tabs, form input, Stop, isolated guards and source transfer.');
  } finally {
    if (session) await session.context.close();
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(root, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error);process.exitCode = 1; });

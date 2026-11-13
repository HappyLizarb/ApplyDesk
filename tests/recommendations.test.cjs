const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {start, dispatch} = require('../browser/worker.cjs');
const {jobLink} = require('../browser/recommendations-url.cjs');
const {armSource} = require('../browser/guard.cjs');
const {readFeed, readJob, waitForJob, followFeedLink, expandDescription} = require('../browser/recommendations-dom.cjs');
const {fixtureServer, handshake, linkedin, feed, description} = require('./recommendations-fixtures.cjs');

// Only current observed URL shapes establish platform IDs; arbitrary redirects cannot create jobs.
function testSourceUrls() {
  assert.equal(jobLink('https://app.joinhandshake.com/job-search/123', 'handshake').id, '123');
  assert.equal(jobLink('https://www.linkedin.com/jobs/search-results/?currentJobId=9005', 'linkedin').id, '9005');
  assert.equal(jobLink('https://www.linkedin.com/jobs/view/engineering-at-acme-9005/', 'linkedin').id, '9005');
  for (const url of ['https://evil.test/job-search/123', 'https://school.joinhandshake.com/jobs/123', 'https://app.joinhandshake.com/stu/jobs/123']) assert.equal(jobLink(url, 'handshake'), null);
  assert.equal(jobLink('https://x:y@www.linkedin.com/jobs/view/123', 'linkedin'), null);
  assert.equal(jobLink('https://www.linkedin.com/jobs/search-results/?currentJobId=nope', 'linkedin'), null);
}

// Verify Chrome's actual launch arguments, not only the Playwright configuration.
async function assertSandboxEnabled(session, headless = true) {
  const page = await session.context.newPage();
  try {
    await page.goto('chrome://version');
    const command = await page.locator('#command_line').innerText();
    assert(!command.includes('--no-sandbox'));
    assert(!command.includes('--disable-setuid-sandbox'));
    for (const flag of ['--password-store=basic', '--use-mock-keychain', '--disable-client-side-phishing-detection',
      '--unsafely-disable-devtools-self-xss-warnings', '--disable-infobars', '--disable-ipc-flooding-protection',
      '--disable-popup-blocking', '--disable-prompt-on-repost', '--enable-unsafe-swiftshader']) assert(!command.includes(flag));
    assert(command.includes('--enable-automation'));
    assert.equal(command.includes('--headless'), headless);
  } finally { await page.close(); }
}

// The fixture's sign-in is performed as an explicit simulated human action, never a worker command.
async function signIn(session, source) {
  const result = await dispatch(session, 'source_login', {});
  if (result.status === 'waiting_signin') {
    await session.page.locator('form button').click();
    await session.page.waitForURL(url => url.pathname === (source === 'handshake' ? '/home' : '/jobs/'));
  }
  const ready = await dispatch(session, 'source_status', {});
  assert.equal(ready.status, 'ready');
  assert.equal(ready.guard.manual, true);
  assert(!JSON.stringify(ready).includes('PRIVATE'));
}

// Page-issued POST hydration and asynchronous expansion preserve the signed-in source context.
async function testHandshakeLifecycle(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'handshake'), source: 'handshake', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await assertSandboxEnabled(session);
    await signIn(session, 'handshake');
    for (const method of ['fill', 'next', 'open', 'snapshot', 'authenticate', 'focus', 'click', 'evaluate', 'submit']) await assert.rejects(dispatch(session, method, {}), /Only recommendation/);
    await assert.rejects(dispatch(session, 'source_scan', {limit: 11}), /1 to 10/);
    const oldContext = session.context;
    const report = await dispatch(session, 'source_scan', {limit: 3});
    assert.equal(session.context, oldContext);
    assert.equal(session.state.manual, false);
    assert.equal(await session.page.evaluate(() => window.fixtureSessionAlive), true);
    assert.equal(await session.page.evaluate(() => window.__applyDesk.manual), false);
    assert.equal(report.jobs.length, 3);
    const complete = report.jobs.find(job => job.id === '9001');
    assert.equal(complete.complete, true, JSON.stringify(complete));
    assert(complete.text.includes(description) && complete.text.includes('US work authorization required'));
    assert.equal(complete.title, 'Engineering Intern');
    assert.equal(complete.company, 'Acme Systems');
    assert.equal(complete.location, 'Onsite, based in San Francisco, CA');
    assert(report.jobs.filter(job => job.id !== '9001').every(job => !job.complete));
    assert(report.jobs.filter(job => job.id !== '9001').every(job => job.title === '' && job.company === '' && job.text === ''));
    assert.equal(report.jobs.find(job => job.id === '9003').url, fixture.origin + '/job-search/9003');
    assert(report.warnings.some(warning => warning.includes('blocked')));
    assert(!JSON.stringify(report).includes('PRIVATE') && !JSON.stringify(report).includes('private@example.test'));
    assert(fixture.writes.includes('/hydrate-job'));
    assert.equal(report.guard.mutations_blocked, false);
    assert.deepEqual([report.guard.page_networking, report.guard.form_submission_blocked], ['normal', true]);
    assert.equal(report.guard.final_paths_blocked, true);
    assert(!fixture.writes.some(path => ['/save', '/submit'].includes(path)));
    assert(!fixture.visits.includes('/job-search/999999'));
    assert.equal((await session.context.cookies()).find(cookie => cookie.name === 'signed').value, '1');
    await assert.rejects(dispatch(session, 'source_job', {url: fixture.origin + '/job-search/9001', id: 'wrong'}), /does not match/);
  } finally { await session.context.close(); }
}

// Source networking continues while current/future forms and explicit final endpoints remain guarded.
async function testRearm(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'rearm'), source: 'handshake', sourceManual: true, headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin);
    await session.page.setContent('<iframe src="' + fixture.origin + '/frame"></iframe>');
    await session.page.frameLocator('iframe').locator('body').waitFor();
    await session.page.evaluate(url => new Promise(resolve => {
      window.socket = new WebSocket(url);socket.onopen = () => socket.send('manual-one');socket.onmessage = () => resolve();
    }), fixture.origin.replace('http:', 'ws:'));
    session.state.auth = {method: 'POST', url: fixture.origin + '/submit', expires: Date.now() + 10000};
    await armSource(session.context, session.state);
    assert.equal(session.state.auth, null);
    await session.page.evaluate(() => new Promise(resolve => { socket.onmessage = resolve;socket.send('reading-two'); }));
    for (const frame of session.page.frames()) assert.equal(await frame.evaluate(() => __applyDesk.manual === false && __applyDesk.authUntil === 0), true);
    await session.page.goto(fixture.origin + '/new-document');
    assert.equal(await session.page.evaluate(() => __applyDesk.manual), false);
    await session.page.evaluate(() => { document.body.innerHTML = '<form method="post" action="/save"><input><button>Submit</button></form>'; });
    await session.page.locator('input').press('Enter');
    await session.page.locator('button').click();
    await session.page.evaluate(() => { document.querySelector('form').submit();document.querySelector('form').requestSubmit(); });
    const sent = await session.page.evaluate(() => fetch('/submit', {method: 'POST'}).then(() => true, () => false));
    assert.equal(sent, false);
    session.state.allowLocal = false;
    const privateSent = await session.page.evaluate(() => fetch('/private', {method: 'POST'}).then(() => true, () => false));
    assert.equal(privateSent, false);
    session.state.allowLocal = true;
    assert.deepEqual(fixture.websocket.messages, ['manual-one', 'reading-two']);
    assert(!fixture.writes.some(path => ['/save', '/submit', '/private'].includes(path)));
  } finally { await session.context.close(); }
}

// A security challenge ends the scan immediately without attempting another recommendation.
async function testChallengeStop(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'challenge'), source: 'handshake', sourceTestOrigin: fixture.origin, sourceManual: true, headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin);
    await session.page.setContent(feed('handshake').replace('/9001', '/666666'));
    const before = fixture.visits.length;
    const report = await dispatch(session, 'source_scan', {limit: 3});
    assert.equal(report.status, 'blocked');
    assert.equal(report.jobs.length, 1);
    assert(report.issues.some(issue => issue.includes('security check')));
    assert(!fixture.visits.slice(before).includes('/job-search/9002'));
    assert(!JSON.stringify(session.state.blocked).includes('private-nonce'));
    assert(!fixture.visits.slice(before).includes('/job-search/9003'));
  } finally { await session.context.close(); }
}

// LinkedIn search-pane and direct-page headers preserve restrictions and observed employer links.
async function testLinkedIn(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'linkedin'), source: 'linkedin', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await signIn(session, 'linkedin');
    const context = session.context;
    const evaluateHandle = session.page.evaluateHandle.bind(session.page);
    session.page.evaluateHandle = (expression, args) => {
      assert(!String(expression).includes('return locateFeedLink(args)'), 'LinkedIn navigation must use the observed URL.');
      return evaluateHandle(expression, args);
    };
    const report = await dispatch(session, 'source_scan', {limit: 10});
    assert.equal(session.context, context);
    assert.equal(report.jobs.length, 2);
    assert(report.jobs.every(job => job.complete), JSON.stringify(report));
    assert.equal(report.jobs[0].complete, true, JSON.stringify(report.jobs[0]));
    assert.equal(report.jobs[0].location, 'San Jose, CA');
    assert.equal(report.jobs[0].employer_url, 'https://jobs.example.test/search/123');
    assert(report.jobs[0].text.includes('at most two applications') && report.jobs[0].text.includes('available internship dates'));
    const fresh = await dispatch(session, 'source_job', {url: fixture.origin + '/jobs/view/9005/', id: '9005'});
    assert.equal(fresh.job.complete, true, JSON.stringify(fresh.job));
    assert.equal(fresh.job.url, fixture.origin + '/jobs/view/9005/');
    assert(!JSON.stringify(fresh).includes('PRIVATE'));
    await assert.rejects(dispatch(session, 'next', {}), /Only recommendation/);
  } finally { await session.context.close(); }
}

// Identity can render before the description; the reader must wait for visible body hydration.
async function testDelayedDescription(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'delayed'), source: 'linkedin', headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin + '/jobs/view/9005/');
    assert.equal(await session.page.evaluate(() => window.fixtureCspEvalBlocked), true);
    session.page.waitForFunction = () => { throw new Error('Source reads must not inject an unsafe-eval polling function.'); };
    await session.page.evaluate(() => {
      const description = document.querySelector('#JobDetails_AboutTheJob_9005 p'), original = description.innerText;
      description.innerText = '';description.setAttribute('aria-busy', 'true');
      setTimeout(() => { description.innerText = original;description.removeAttribute('aria-busy'); }, 300);
    });
    await waitForJob(session.page, 'linkedin', '9005', fixture.origin);
    const job = await readJob(session.page, 'linkedin', '9005', fixture.origin);
    assert.equal(job.complete, true, JSON.stringify(job));
    assert(job.text.includes(description));
  } finally { await session.context.close(); }
}

// Handshake's SPA anchor retry is bounded and never repeats a completed navigation.
async function testDetachedFeed(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'detached'), source: 'handshake', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await signIn(session, 'handshake');
    await armSource(session.context, session.state);
    const candidate = (await readFeed(session.page, 'handshake', fixture.origin)).links[0];
    const original = session.page.evaluateHandle.bind(session.page), before = fixture.visits.length;
    let replacements = 0;
    session.page.evaluateHandle = async expression => {
      const handle = await original(expression);
      if (expression.includes('return locateFeedLink(args)') && replacements < 2) {
        await handle.evaluate(anchor => anchor.replaceWith(anchor.cloneNode(true)));
        replacements++;
      }
      return handle;
    };
    await followFeedLink(session.page, candidate, 'handshake', fixture.origin);
    assert.equal(replacements, 2);
    assert.equal(new URL(session.page.url()).pathname, '/job-search/' + candidate.id);
    assert.equal(fixture.visits.slice(before).filter(url => url === '/job-search/' + candidate.id).length, 1);
    const count = fixture.visits.length;
    await followFeedLink(session.page, candidate, 'handshake', fixture.origin);
    assert.equal(fixture.visits.length, count);
  } finally { await session.context.close(); }
}

// Collapsed, duplicate, and loading content cannot masquerade as a complete job capture.
async function testIncompleteSurfaces(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'incomplete'), source: 'handshake', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin + '/job-search/9001');
    await session.page.setContent(handshake('9001').replace('<title>Engineering Intern | Handshake</title>', '<title>Security Verification</title>'));
    await expandDescription(session.page, 'handshake', '9001', fixture.origin);
    assert.equal(await session.page.locator('.view-more-button').count(), 1);
    assert.equal(await session.page.locator('.view-more-button').isEnabled(), true);
    await session.page.setContent(handshake('9001').replace('type="button" class="view-more-button"', 'type="submit" class="view-more-button"'));
    await expandDescription(session.page, 'handshake', '9001', fixture.origin);
    assert.equal((await readJob(session.page, 'handshake', '9001', fixture.origin)).complete, false);
    await session.page.setContent(handshake('777777', false));
    assert.equal((await readJob(session.page, 'handshake', '9001', fixture.origin)).complete, false);
    await session.page.setContent(handshake('9001', false).replace('<p>' + description, '<p aria-busy="true">' + description));
    assert.equal((await readJob(session.page, 'handshake', '9001', fixture.origin)).complete, false);
    await session.page.setContent(feed('handshake').replace('Job picks for', 'Recommendations unfamiliar heading'));
    assert.equal((await readFeed(session.page, 'handshake', fixture.origin)).ready, false);
    await session.page.setContent('<title>Security Verification</title>' + feed('handshake'));
    assert.equal((await readFeed(session.page, 'handshake', fixture.origin)).challenge, true);
    session.state.manual = true;
    assert.equal((await dispatch(session, 'source_status', {})).status, 'waiting_signin');
    await session.page.setContent('<h2>Job picks for engineering</h2>');
    assert.equal((await dispatch(session, 'source_status', {})).status, 'waiting_signin');
    session.state.manual = false;
    await session.page.setContent(handshake('9001', false).replace(description, 'x'.repeat(50001)));
    const oversized = await readJob(session.page, 'handshake', '9001', fixture.origin);
    assert.equal(oversized.complete, false);
    assert.equal(oversized.text, '');
  } finally { await session.context.close(); }
}

// A close response must release the JSONL input loop as well as the browser context.
async function testWorkerClose(directory) {
  const options = {dataRoot: path.join(directory, 'close'), source: 'handshake', headless: true};
  const child = spawn(process.execPath, [path.join(__dirname, '../browser/worker.cjs')], {env: {...process.env, APPLYDESK_BROWSER_OPTIONS: JSON.stringify(options)}, stdio: ['pipe', 'pipe', 'pipe']});
  const exit = new Promise(resolve => child.once('exit', resolve));
  child.stdin.write(JSON.stringify({id: 'close', method: 'close'}) + '\n');
  let timeout;
  try {
    const code = await Promise.race([exit, new Promise((_, reject) => { timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Source worker remained alive after close.')); }, 10000); })]);
    assert.equal(code, 0);
  } finally { clearTimeout(timeout); }
}

// Termination interrupts an in-flight source read and releases its dedicated Chrome profile.
async function testWorkerCancellation(directory, fixture) {
  const options = {dataRoot: path.join(directory, 'cancel'), source: 'handshake', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true};
  const child = spawn(process.execPath, [path.join(__dirname, '../browser/worker.cjs')], {env: {...process.env, APPLYDESK_BROWSER_OPTIONS: JSON.stringify(options)}, stdio: ['pipe', 'pipe', 'pipe']});
  const exit = new Promise(resolve => child.once('exit', resolve));
  child.stdin.write(JSON.stringify({id: 'slow', method: 'source_job', params: {url: fixture.origin + '/job-search/555555', id: '555555'}}) + '\n');
  for (let count = 0; count < 100 && !fixture.visits.includes('/job-search/555555'); count++) await new Promise(resolve => setTimeout(resolve, 100));
  assert(fixture.visits.includes('/job-search/555555'));
  child.kill('SIGTERM');
  let timeout;
  try { await Promise.race([exit, new Promise((_, reject) => { timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Source worker cancellation did not finish.')); }, 5000); })]); }
  finally { clearTimeout(timeout); }
  const reopened = await start(options);
  await reopened.context.close();
}

async function main() {
  testSourceUrls();
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-recommendations-'));
  const fixture = await fixtureServer();
  try {
    await testHandshakeLifecycle(directory, fixture);
    await testLinkedIn(directory, fixture);
    await testRearm(directory, fixture);
    await testChallengeStop(directory, fixture);
    await testDelayedDescription(directory, fixture);
    await testDetachedFeed(directory, fixture);
    await testIncompleteSurfaces(directory, fixture);
    await testWorkerClose(directory);
    await testWorkerCancellation(directory, fixture);
    assert(!fixture.writes.some(url => ['/save', '/submit', '/private'].includes(url)));
    console.log('Recommendation fixtures passed: source identities, manual login, persistent cookies, normal POST/socket hydration, preserved source sessions, scoped text, delayed expansion, incomplete captures, form/final-path blocking, cancellation.');
  } finally {
    fixture.server.closeAllConnections();
    await new Promise(resolve => fixture.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

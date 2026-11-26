const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {start, dispatch} = require('../browser/worker.cjs');
const {jobLink} = require('../browser/recommendations-url.cjs');
const {readJob, waitForJob, expandDescription} = require('../browser/recommendations-dom.cjs');
const {fixtureServer, handshake, description} = require('./recommendations-fixtures.cjs');

// Only current observed URL shapes establish platform IDs; arbitrary redirects cannot create jobs.
function testSourceUrls() {
  assert.equal(jobLink('https://app.joinhandshake.com/job-search/123', 'handshake').id, '123');
  assert.equal(jobLink('https://www.linkedin.com/jobs/search-results/?currentJobId=20002', 'linkedin').id, '20002');
  assert.equal(jobLink('https://www.linkedin.com/jobs/view/engineering-at-acme-20002/', 'linkedin').id, '20002');
  for (const url of ['https://evil.test/job-search/123', 'https://school.joinhandshake.com/jobs/123', 'https://app.joinhandshake.com/stu/jobs/123']) assert.equal(jobLink(url, 'handshake'), null);
  assert.equal(jobLink('https://x:y@www.linkedin.com/jobs/view/123', 'linkedin'), null);
  assert.equal(jobLink('https://www.linkedin.com/jobs/search-results/?currentJobId=nope', 'linkedin'), null);
}

// Verify Chrome's actual launch arguments, not only the Playwright configuration.
async function assertSandboxEnabled(session) {
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
    assert(command.includes('--headless'));
  } finally { await page.close(); }
}

// Identity can render before the description; the reader must wait for visible body hydration.
async function testDelayedDescription(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'delayed'), source: 'linkedin', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await assertSandboxEnabled(session);
    const url = fixture.origin + '/jobs/view/20002/';
    await assert.rejects(dispatch(session, 'source_job', {url, id:'999'}), /does not match/);
    const capture = await dispatch(session, 'source_job', {url, id:'20002'});
    assert.equal(capture.status, 'ready');
    assert.equal(capture.job.id, '20002');
    assert.equal(await session.page.evaluate(() => window.fixtureCspEvalBlocked), true);
    session.page.waitForFunction = () => { throw new Error('Source reads must not inject an unsafe-eval polling function.'); };
    await session.page.evaluate(() => {
      const description = document.querySelector('#JobDetails_AboutTheJob_20002 p'), original = description.innerText;
      description.innerText = '';description.setAttribute('aria-busy', 'true');
      setTimeout(() => { description.innerText = original;description.removeAttribute('aria-busy'); }, 300);
    });
    await waitForJob(session.page, 'linkedin', '20002', fixture.origin);
    const job = await readJob(session.page, 'linkedin', '20002', fixture.origin);
    assert.equal(job.complete, true, JSON.stringify(job));
    assert(job.text.includes(description));
  } finally { await session.context.close(); }
}

// Changed or incomplete detail pages cannot establish a saved job identity.
async function testIncompleteSurfaces(directory, fixture) {
  const session = await start({dataRoot: path.join(directory, 'incomplete'), source: 'handshake', sourceTestOrigin: fixture.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin + '/job-search/10001');
    await session.page.setContent(handshake('10001').replace('<title>Engineering Intern | Handshake</title>', '<title>Security Verification</title>'));
    await expandDescription(session.page, 'handshake', '10001', fixture.origin);
    assert.equal(await session.page.locator('.view-more-button').count(), 1);
    assert.equal(await session.page.locator('.view-more-button').isEnabled(), true);
    await session.page.setContent(handshake('10001').replace('type="button" class="view-more-button"', 'type="submit" class="view-more-button"'));
    await expandDescription(session.page, 'handshake', '10001', fixture.origin);
    assert.equal((await readJob(session.page, 'handshake', '10001', fixture.origin)).complete, false);
    await session.page.setContent(handshake('777777', false));
    assert.equal((await readJob(session.page, 'handshake', '10001', fixture.origin)).complete, false);
    await session.page.setContent(handshake('10001', false).replace('<p>' + description, '<p aria-busy="true">' + description));
    assert.equal((await readJob(session.page, 'handshake', '10001', fixture.origin)).complete, false);
    await session.page.setContent(handshake('10001', false).replace(description, 'x'.repeat(50001)));
    const oversized = await readJob(session.page, 'handshake', '10001', fixture.origin);
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
    await testDelayedDescription(directory, fixture);
    await testIncompleteSurfaces(directory, fixture);
    await testWorkerClose(directory);
    await testWorkerCancellation(directory, fixture);
    assert(!fixture.writes.some(url => ['/save', '/submit', '/private'].includes(url)));
    console.log('Saved-source identity, content, cancellation and closure checks passed.');
  } finally {
    fixture.server.closeAllConnections();
    await new Promise(resolve => fixture.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {createRequire} = require('node:module');
const moduleRoot = process.env.APPLYDESK_NODE_MODULES;
const load = moduleRoot ? createRequire(path.join(moduleRoot, '__applydesk__.cjs')) : require;
const {chromium} = load('playwright');
const {inspect} = require('./inspect.cjs');
const {assertPublicUrl, installGuards} = require('./guard.cjs');
const {fill, advance, authenticate} = require('./actions.cjs');
const {sourceCommand} = require('./recommendations.cjs');
const {applicationEntry, submitApplication} = require('./submission.cjs');
const {jobLink} = require('./recommendations-url.cjs');
const {waitForJob} = require('./recommendations-dom.cjs');
const {show} = require('./window.cjs');

// A personal browser must keep native credential protection and security warnings enabled.
const SECURITY_TEST_FLAGS = ['--password-store=basic', '--use-mock-keychain',
  '--disable-client-side-phishing-detection', '--unsafely-disable-devtools-self-xss-warnings',
  '--disable-infobars', '--disable-ipc-flooding-protection', '--disable-popup-blocking',
  '--disable-prompt-on-repost', '--enable-unsafe-swiftshader'];

// A dedicated Chrome profile keeps each application's live handoff session intact.
async function start(options) {
  if (options.source && !['linkedin', 'handshake'].includes(options.source)) throw new Error('Unknown recommendation source.');
  if (options.research && (options.source || options.applicationSource)) throw new Error('Research uses a separate browser profile.');
  fs.mkdirSync(options.dataRoot, {recursive: true});
  const headless = !!options.headless;
  const context = await chromium.launchPersistentContext(options.dataRoot, {
    headless, executablePath: options.executablePath || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    chromiumSandbox: true, ignoreHTTPSErrors: false, ignoreDefaultArgs: SECURITY_TEST_FLAGS,
    viewport: {width: 1280, height: 900}, serviceWorkers: 'block', acceptDownloads: false,
    args: ['--enable-automation', '--disable-background-networking', '--disable-sync', '--no-first-run'],
  });
  const state = {manual: !!(options.source && options.sourceManual), source: options.source || null, research: !!options.research,
    allowLocal: !!options.allowLocal, blocked: [], auth: null, applicationSource: options.applicationSource || null, phase: 'reading',
    uploadRoots: options.uploadRoots || [options.dataRoot]};
  await installGuards(context, state);
  const page = context.pages()[0] || await context.newPage();
  await page.goto('about:blank');
  return {context, page, state, options};
}

// Explicit sign-in starts a manual source session; reading preserves that same browser afterwards.
async function restartSource(session, manual) {
  if (!session.state.source) throw new Error('Only a dedicated recommendation session can be reopened.');
  const original = session.options;
  const options = {...original, sourceManual: manual};
  await session.context.close();
  Object.assign(session, await start(options));
  session.options = original;
}

// Handoff is explicit; automation cannot resume by silently taking over a user's page.
async function focus(session, params) {
  if (params.manual !== true) throw new Error('focus requires manual:true to explicitly hand control to the user.');
  session.state.manual = true;
  await Promise.all(session.context.pages().map(page => page.evaluate(() => {
    if (globalThis.__applyDesk) globalThis.__applyDesk.manual = true;
  }).catch(() => {})));
  await session.page.bringToFront();
  return {...await inspect(session.page, session.state), handoff: true};
}

// The exposed command set contains no arbitrary script, keypress, or generic click tool.
async function dispatch(session, method, params) {
  const {page, state} = session;
  if (state.research && !['open', 'snapshot', 'close'].includes(method)) throw new Error('Web research supports page reads. Use the application workflow for forms.');
  if (state.source && method !== 'close') return sourceCommand(session, method, params, restartSource);
  if (method.startsWith('source_')) throw new Error('A dedicated recommendation session is required.');
  if (method === 'open') {
    if (state.manual) throw new Error('Close and reopen this browser session before resuming automation.');
    await assertPublicUrl(params.url, state.allowLocal);
    if (!session.options.headless && !state.research) await show(session);
    state.blocked = [];
    await page.goto(params.url, {waitUntil: 'domcontentloaded', timeout: 30000});
    if (state.research) await settleResearchPage(page);
    if (state.applicationSource && !state.allowLocal) {
      const job = jobLink(params.url, state.applicationSource);
      if (!job) throw new Error('The platform application needs an observed job listing URL.');
      await waitForJob(page, state.applicationSource, job.id, new URL(job.url).origin);
    }
    return inspect(page, state);
  }
  if (method === 'snapshot') return inspect(page, state);
  if (method === 'show') return show(session);
  if (method === 'fill') {
    if (state.applicationSource) state.phase = 'editing';
    return fill(page, state, params);
  }
  if (method === 'next') return advance(page, state, params);
  if (method === 'authenticate') return authenticate(page, state, params);
  if (method === 'application_entry') return applicationEntry(page, state, params);
  if (method === 'submit') return submitApplication(page, state, params);
  if (method === 'focus') return focus(session, params);
  if (method === 'close') { await session.context.close(); return {closed: true}; }
  throw new Error('Unknown browser command.');
}

// Read after client rendering settles, while bounding pages with continuous background traffic.
async function settleResearchPage(page) {
  await page.waitForLoadState('networkidle', {timeout: 10000}).catch(() => {});
  let previous = '', stable = 0;
  for (let index = 0; index < 12; index++) {
    await page.waitForTimeout(250);
    const text = await page.locator('body').innerText({timeout: 3000});
    stable = text.length >= 150 && text === previous ? stable + 1 : 0;
    if (stable >= 2) return;
    previous = text;
  }
}

// Remove submitted values from driver exceptions before returning them to application logs.
function safeError(error, params) {
  let message = String(error.message || error).split('Call log:')[0].slice(0, 600);
  for (const answer of params?.answers || []) {
    if (typeof answer.value === 'string' && answer.value.length > 1) message = message.split(answer.value).join('[redacted]');
  }
  return message;
}

// A serial JSONL protocol prevents two requests from racing the same browser page.
async function main() {
  const options = JSON.parse(process.env.APPLYDESK_BROWSER_OPTIONS || '{}');
  if (!options.dataRoot) throw new Error('Browser data folder is required.');
  const session = await start(options);
  process.once('SIGTERM', () => { session.context.close().finally(() => process.exit(0)); });
  const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
  for await (const line of input) {
    let request;
    try {
      request = JSON.parse(line);
      const result = await dispatch(session, request.method, request.params || {});
      process.stdout.write(JSON.stringify({id: request.id, result}) + '\n');
      if (request.method === 'close') { input.close(); return; }
    } catch (error) { process.stdout.write(JSON.stringify({id: request?.id ?? null, error: safeError(error, request?.params)}) + '\n'); }
  }
  await session.context.close();
}

if (require.main === module) main().catch(error => { process.stderr.write(safeError(error) + '\n'); process.exitCode = 1; });
module.exports = {start, dispatch};

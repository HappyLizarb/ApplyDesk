const {readWorkdayChoices}=require('./workday-controls.cjs');
const {agentObserve}=require('./agent-page.cjs');
const {agentAction}=require('./agent-page-actions.cjs');
const {agentAuthenticate}=require('./agent-page-auth.cjs');
const {agentSubmit}=require('./agent-submission.cjs');
const {waitAgentRendering}=require('./agent-page-rendering.cjs');
const {settleResearchPage} = require('./page-settling.cjs');
const fs = require('node:fs');
const path = require('node:path');
const {trackAshbyUploads} = require('./ashby-upload.cjs');
const {trackAshbyForms} = require('./ashby-drafts.cjs');
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
const {workspaceCommand} = require('./workspace.cjs');
const {sourceStorage} = require('./source-storage.cjs');
const {checkpoint} = require('./control.cjs');
const {runProtocol} = require('./protocol.cjs');
const {openApplicationFrame} = require('./application-frame.cjs');
const {trackUploads} = require('./greenhouse-upload.cjs');
const {continueVerification} = require('./verification.cjs');
const {connectExistingChrome,releaseSession,applicationWindow}=require('./existing-chrome.cjs');

// A personal browser must keep native credential protection and security warnings enabled.
const SECURITY_TEST_FLAGS = ['--password-store=basic', '--use-mock-keychain',
  '--disable-client-side-phishing-detection', '--unsafely-disable-devtools-self-xss-warnings',
  '--disable-infobars', '--disable-ipc-flooding-protection', '--disable-popup-blocking',
  '--disable-prompt-on-repost', '--enable-unsafe-swiftshader'];

// A dedicated Chrome profile keeps each application's live handoff session intact.
async function start(options,startup={}) {
  if (options.source && !['linkedin', 'handshake'].includes(options.source)) throw new Error('Unknown recommendation source.');
  if (options.research && (options.source || options.applicationSource)) throw new Error('Research uses a separate browser profile.');
  if (options.applicationWorkspace && (options.research || options.source || options.applicationSource)) throw new Error('Applications use their own browser workspace.');
  fs.mkdirSync(options.dataRoot, {recursive: true});
  const state = initialState(options);
  if(options.existingChrome)return connectExistingChrome(chromium,options,state,startup.signal);
  const headless = !!options.headless;
  const context = await chromium.launchPersistentContext(options.dataRoot, {
    headless, executablePath: options.executablePath || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    chromiumSandbox: true, ignoreHTTPSErrors: false, ignoreDefaultArgs: SECURITY_TEST_FLAGS,
    viewport: options.applicationWorkspace ? null : {width: 1280, height: 900}, serviceWorkers: 'block', acceptDownloads: false,
    args: ['--enable-automation', '--disable-background-networking', '--disable-sync', '--no-first-run'],
  });
  await installGuards(context, state);
  const page = context.pages()[0] || await context.newPage();
  await page.goto('about:blank');
  return {context, page, state, options, ...(options.applicationWorkspace ? {tabs: new Map()} : {})};
}

// Ownership and network mode are explicit; a connected personal profile is never treated as ours.
function initialState(options) {
  return {manual:false,source:options.source||null,research:!!options.research,
    allowLocal:!!options.allowLocal,blocked:[],auth:null,applicationSource:options.applicationSource||null,phase:'reading',
    uploadRoots:options.uploadRoots||[options.dataRoot],normalNetworking:!!options.existingChrome,
    ...(options.applicationWorkspace?{pages:new Map(),paused:true}:{})};
}

// Handoff is explicit; automation cannot resume by silently taking over a user's page.
async function focus(session, params) {
  if (params.manual !== true) throw new Error('focus requires manual:true to explicitly hand control to the user.');
  session.state.manual = true;
  const pages = session.state.workspace ? [session.page] : session.context.pages();
  await Promise.all(pages.map(page => page.evaluate(() => {
    if (globalThis.__applyDesk) globalThis.__applyDesk.manual = true;
  }).catch(() => {})));
  await session.page.bringToFront();
  return {...await inspect(session.page, session.state), handoff: true};
}

// The exposed command set contains no arbitrary script, keypress, or generic click tool.
async function dispatch(session, method, params) {
  if (session.options.applicationWorkspace && method === 'verify_application') return continueVerification(session, params, params.code);
  if (session.options.applicationWorkspace && method !== 'close') return workspaceCommand(session, method, params, execute);
  return execute(session, method, params);
}

// Application workspaces share this same bounded command implementation with legacy sessions.
async function execute(session, method, params) {
  const {page, state} = session;
  if (state.research && !['open', 'snapshot', 'close'].includes(method)) throw new Error('Web research supports page reads. Use the application workflow for forms.');
  if (state.source && method === 'source_storage') return sourceStorage(session);
  if (state.source && method !== 'close') return sourceCommand(session, method, params);
  if (method.startsWith('source_')) throw new Error('A dedicated recommendation session is required.');
  if (method === 'open') return openPage(session, params);
  if (method === 'snapshot') return inspect(page, state);
  if (method === 'agent_observe') return agentObserve(page,state);
  if (method === 'agent_action') return agentAction(page,state,params);
  if (method === 'agent_authenticate') return agentAuthenticate(page,state,params);
  if (method === 'agent_submit') return agentSubmit(page,state,params);
  if (method === 'read_choices') return readWorkdayChoices(page,state,params);
  if (method === 'show') {
    if(session.externalChrome)await applicationWindow(session,page);
    return show(session, params);
  }
  if (method === 'fill') {
    if (state.applicationSource) state.phase = 'editing';
    return fill(page, state, params);
  }
  if (method === 'next') return advance(page, state, params);
  if (method === 'authenticate') return authenticate(page, state, params);
  if (method === 'application_entry') return applicationEntry(page, state, params);
  if (method === 'submit') return submitApplication(page, state, params);
  if (method === 'focus') return focus(session, params);
  if (method === 'close') { await releaseSession(session); return {closed: true}; }
  throw new Error('Unknown browser command.');
}

// Stop is checked after presentation and DNS awaits, immediately before navigation.
async function openPage(session, params) {
  const {page, state} = session, check = checkpoint(state);
  check();
  if (state.manual) throw new Error('Close and reopen this browser session before resuming automation.');
  await assertPublicUrl(params.url, state.allowLocal);
  if(session.externalChrome)await applicationWindow(session,page);
  if (!session.options.headless && !state.research) await show(session, params);
  check();state.blocked = [];
  trackUploads(page, state);
  trackAshbyForms(page, state);
  trackAshbyUploads(page, state);
  await page.goto(params.url, {waitUntil: 'domcontentloaded', timeout: 30000, signal: check.signal});
  check();
  const renderDeadline = Date.now() + 8000;
  if (params.agent_driven) await waitAgentRendering(page,state);
  if (!state.applicationSource && !params.agent_driven) await settleResearchPage(page);
  const embedded = !state.research && !state.applicationSource ? await openApplicationFrame(session, check) : null;
  if (embedded && !params.agent_driven) await settleResearchPage(page);
  if (embedded && params.agent_driven) await waitAgentRendering(page,state,{timeoutMs:Math.max(0,renderDeadline-Date.now())});
  if (state.applicationSource && !state.allowLocal) {
    const job = jobLink(params.url, state.applicationSource);
    if (!job) throw new Error('The platform application needs an observed job listing URL.');
    await waitForJob(page, state.applicationSource, job.id, new URL(job.url).origin);
  }
  check();
  const snapshot = await inspect(page, state);
  return embedded ? {...snapshot, application_frame: embedded} : snapshot;
}



// Remove submitted values from driver exceptions before returning them to application logs.
function safeError(error, params) {
  let message = String(error.message || error).split('Call log:')[0].slice(0, 600);
  if (params?.code) message = message.split(params.code).join('[redacted]');
  for (const answer of params?.answers || []) {
    if (typeof answer.value === 'string' && answer.value.length > 1) message = message.split(answer.value).join('[redacted]');
  }
  return message;
}

// A serial JSONL protocol prevents two requests from racing the same browser page.
async function main() {
  const options = JSON.parse(process.env.APPLYDESK_BROWSER_OPTIONS || '{}');
  if (!options.dataRoot) throw new Error('Browser data folder is required.');
  await runProtocol(options, start, dispatch, safeError);
}

if (require.main === module) main().catch(error => { process.stderr.write(safeError(error) + '\n'); process.exitCode = 1; });
module.exports = {start, dispatch};

const {workdayUploadRequest}=require('./workday-upload.cjs');
const {educationRequest}=require('./ashby-education-network.cjs');
const {rememberWorkdayDefinition,workdayDraftRequest}=require('./workday-drafts.cjs');
const {successFactorsAuthRequest} = require('./successfactors-auth.cjs');
const {successFactorsRead} = require('./successfactors-reads.cjs');
const {successFactorsEntry, successFactorsHandoff} = require('./successfactors.cjs');
const {ashbySubmissionRequest} = require('./ashby-submission.cjs');
const {ashbyUploadRequest, ashbyAttachRequest, waitForAshbyUploadPolicy} = require('./ashby-upload.cjs');
const {ashbyDraftRequest} = require('./ashby-drafts.cjs');
const {draftInitialization, workdayFormDefinition} = require('./workday.cjs');
const dns = require('node:dns').promises;
const {workdayAuthRequest} = require('./workday-auth.cjs');
const net = require('node:net');
const {authorizedUpload, uploadedResumeMatches} = require('./greenhouse-upload.cjs');
const {publicFormQuery} = require('./ashby.cjs');
const FINAL_PATH = /\/(submit|finalize|complete-application|send-application)(\/|$)/i;

// Normal browsing gates explicit final controls; legacy sessions also block ordinary form posts.
function browserGuard(options = false) {
  const manual = typeof options === 'boolean' ? options : !!options.manual;
  const state = {ids: new WeakMap(), elements: new Map(), counter: 0,
    documentId: [...crypto.getRandomValues(new Uint32Array(4))].map(value => value.toString(16)).join(''), manual, normalNetworking: !!options?.normalNetworking, paused: false, authUntil: 0, authTarget: null, finalUntil: 0, finalId: null};
  globalThis.__applyDesk = state;
  const permitted = () => !state.paused && (state.manual || Date.now() < state.authUntil);
  const finalControl = element => /submit|send application|finish|complete application/i.test(element?.innerText || element?.value || element?.getAttribute('aria-label') || '');
  const ordinary = (form, submitter) => state.normalNetworking && !state.paused && (submitter ? !finalControl(submitter) : ![...form.querySelectorAll('button,input[type="submit"],[role="button"]')].some(finalControl));
  document.addEventListener('submit', event => {
    const final = !state.paused && Date.now() < state.finalUntil && state.elements.get(state.finalId) === event.submitter;
    const entry = !state.paused && Date.now() < state.entryUntil && state.entryForm === event.target;
    if (!permitted() && !final && !entry && !ordinary(event.target, event.submitter)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('click', event => {
    const button = event.target.closest?.('button,input[type="submit"],[role="button"]');
    const authentication = permitted() && state.authTarget === button;
    if (!state.manual && !authentication && finalControl(button) && !(!state.paused && Date.now() < state.finalUntil && state.elements.get(state.finalId) === button)) {
      event.preventDefault();event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('keydown', event => {
    if (!state.manual && !state.normalNetworking && event.key === 'Enter') { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  for (const name of ['submit', 'requestSubmit']) {
    const original = HTMLFormElement.prototype[name];
    HTMLFormElement.prototype[name] = function (...args) {
      const final = !state.paused && Date.now() < state.finalUntil && state.elements.get(state.finalId)?.form === this;
      const entry = !state.paused && Date.now() < state.entryUntil && state.entryForm === this;
      if (permitted() || final || entry || ordinary(this, args[0])) return original.apply(this, args);
    };
  }
  return state.manual === false && state.authUntil === 0;
}

// Deny special-use IPs, including IPv4-mapped IPv6 and common local network ranges.
function privateAddress(address) {
  const lower = address.toLowerCase().replace(/^\[|\]$/g, '');
  if (lower.startsWith('::ffff:')) return privateAddress(lower.slice(7));
  if (net.isIP(lower) === 6) return !/^[23][0-9a-f]{3}:/.test(lower) || /^2001:db8:/.test(lower);
  if (!net.isIP(lower)) return true;
  const [a, b] = lower.split('.').map(Number);
  return a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) || (a === 192 && [0, 168].includes(b)) ||
    (a === 100 && b >= 64 && b <= 127) || (a === 198 && [18, 19, 51].includes(b)) || (a === 203 && b === 0);
}

// Public-host verification applies to redirects and subresources, not only job links.
async function assertPublicUrl(raw, allowLocal = false) {
  const url = new URL(raw);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Only HTTP(S) URLs without embedded credentials are allowed.');
  if (allowLocal) return url;
  const hostname = url.hostname.replace(/^\[|\]$/g, '');
  if (/^(localhost|.*\.localhost|.*\.local)$/i.test(hostname)) throw new Error('Private network URLs are blocked.');
  const addresses = net.isIP(hostname) ? [{address: hostname}] : await dns.lookup(hostname, {all: true});
  if (!addresses.length || addresses.some(item => privateAddress(item.address))) throw new Error('Private network URLs are blocked.');
  return url;
}

// Logs retain method and destination without query strings, credentials, or bodies.
function recordBlocked(state, request, reason) {
  const url = new URL(request.url());
  const pathname = url.pathname.startsWith('/cdn-cgi/challenge-platform/') ? '/cdn-cgi/challenge-platform/[redacted]' : url.pathname;
  const entry = {method: request.method(), url: `${url.origin}${pathname}`, reason};
  if (url.origin === 'https://jobs.ashbyhq.com' && pathname === '/api/non-user-graphql') {
    try {
      const payload = request.postDataJSON();
      entry.operation = typeof payload.operationName === 'string' ? payload.operationName.slice(0,100) : '';
      entry.variable_names = Object.keys(payload.variables || {}).slice(0,30);
    } catch { /* Candidate values and malformed request bodies are never logged. */ }
  }
  if (!state.blocked.some(item => JSON.stringify(item) === JSON.stringify(entry))) state.blocked.push(entry);
  state.blocked = state.blocked.slice(-40);
}

// Owned tabs keep Chrome's resource policy; legacy sessions still deny unverified writes.
async function routeRequest(route, state) {
  const request = route.request();
  if (state.normalNetworking) return normalApplicationRequest(route, state);
  try { await assertPublicUrl(request.url(), state.allowLocal); }
  catch (error) { recordBlocked(state, request, error.message); return route.abort('blockedbyclient'); }
  const url = new URL(request.url());
  const finalPath = FINAL_PATH.test(url.pathname);
  const mutation = !['GET', 'HEAD', 'OPTIONS'].includes(request.method());
  if (state.paused && (mutation || finalPath)) {
    recordBlocked(state, request, 'Application paused.');return route.abort('blockedbyclient');
  }
  if (state.manual) return route.continue();
  const applicationPermit=await permittedApplicationRequest(request,state);
  if(applicationPermit)return applicationPermit===true?route.continue():route.continue(applicationPermit);
  if (mutation && !finalPath && telemetry(url)) {
    recordBlocked(state, request, 'Unverified page telemetry.');return route.abort('blockedbyclient');
  }
  // Research has no account cookies, file uploads, form actions, or application commands.
  if (state.research && !finalPath) return route.continue();
  const permit = state.submission;
  const upload = mutation && await authorizedUpload(request, state);
  if (upload) return route.continue(upload);
  if (nativeAssessment(request, url, permit)) return route.continue();
  if (submissionRequest(request, state)) return route.continue();
  if (state.applicationSource && !state.source) return applicationRequest(route, state, finalPath, mutation);
  if (successFactorsAuthRequest(request,state.auth) || workdayAuthRequest(request, state.auth)) return route.continue();
  const auth = state.auth && !state.auth.workday && !state.auth.successfactors && Date.now() < state.auth.expires && request.method() === state.auth.method && request.url() === state.auth.url;
  if (!finalPath && (state.source || !mutation || auth)) return route.continue();
  recordBlocked(state, request, finalPath ? 'Possible final submission endpoint.' : 'Unverified mutation requires manual control.');
  return route.abort('blockedbyclient');
}

// Employer traffic renders normally; recognized operations still retain response evidence.
async function normalApplicationRequest(route, state) {
  const request = route.request();
  const applicationPermit = await permittedApplicationRequest(request, state);
  if (applicationPermit) return applicationPermit === true ? route.continue() : route.continue(applicationPermit);
  const upload = !['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && await authorizedUpload(request, state);
  if (upload) return route.continue(upload);
  submissionRequest(request, state);
  return route.continue();
}

// Record only the current reserved application's request for subsequent receipt verification.
function submissionRequest(request, state) {
  const permit = state.submission, url = new URL(request.url());
  if (!permit || Date.now() >= permit.expires || permit.used || ['GET', 'HEAD', 'OPTIONS'].includes(request.method())
      || url.origin !== permit.origin || permit.url && request.url() !== permit.url) return false;
  if (!(permit.ashby ? ashbySubmissionRequest(request, permit) : !permit.greenhouse || uploadedResumeMatches(request, state))) return false;
  permit.used = true;
  return true;
}

// Recognized employer reads, audited draft saves and exact-byte uploads share one scoped gate.
async function permittedApplicationRequest(request,state) {
  await waitForAshbyUploadPolicy(request, state);
  const workdayUpload=await workdayUploadRequest(request,state);
  if(workdayUpload)return workdayUpload;
  if (workdayFormDefinition(request)) {rememberWorkdayDefinition(request,state);return true;}
  if (workdayDraftRequest(request,state)) return true;
  if (educationRequest(request,state) || successFactorsRead(request,state) || successFactorsEntry(request, state) || successFactorsHandoff(request,state) || publicFormQuery(request) || draftInitialization(request) || ashbyDraftRequest(request, state) || ashbyUploadRequest(request, state) || ashbyAttachRequest(request, state)) return true;
  return false;
}

// Native invisible assessment is allowed only during an authorized Greenhouse submit.
function nativeAssessment(request, url, permit) {
  const active = (permit?.greenhouse || permit?.ashby) && Date.now() < permit.expires;
  const endpoint = ['www.recaptcha.net', 'www.google.com'].includes(url.hostname)
    && url.pathname === '/recaptcha/enterprise/reload';
  return active && endpoint && request.method() === 'POST';
}

// Known analytics failures are not application failures; their requests remain blocked.
function telemetry(url) {
  const hosts = ['browser-intake-datadoghq.com', 'csp-report.browser-intake-datadoghq.com', 'sessions.bugsnag.com', 'analytics.google.com', 'www.google-analytics.com'];
  const workday = /^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(url.hostname)
    && /^\/wday\/calypso\/cxs\/jobapplication\/[a-z0-9-]+\/package\/[a-f0-9]{32}\/trackdropoff$/.test(url.pathname);
  const errorLog = /^wd\d+\.myworkday\.com$/.test(url.hostname) && url.pathname==='/wday/microscope/prelogin/mscp_error_kit_logs';
  return workday || errorLog || hosts.includes(url.hostname) || (url.hostname === 'www.google.com' && url.pathname === '/g/collect')
    || (url.hostname === 'c.spl.greenhouse.io' && url.pathname === '/com.snowplowanalytics.snowplow/tp2');
}

// Platform rendering uses its normal page requests; form writes remain narrowly bounded.
function applicationRequest(route, state, finalPath, mutation) {
  const request = route.request(), url = new URL(request.url());
  const body = request.postData() || '';
  const terminal = finalPath || /submit.?application|send.?application|create.?job.?application/i.test(url.pathname + body);
  if (!terminal && !mutation) return route.continue();
  const host = state.applicationSource === 'handshake' ? 'joinhandshake.com' : 'linkedin.com';
  const trusted = url.hostname === host || url.hostname.endsWith('.' + host);
  const reading = !/\bmutation\b/.test(body) && /\bquery\b/.test(body);
  const attachment = state.phase === 'editing' && /\/(documents|attachments|uploads|resumes)(\/|$)/i.test(url.pathname);
  if (!terminal && trusted && (state.phase === 'reading' || reading || attachment)) return route.continue();
  recordBlocked(state, request, terminal ? 'Possible final submission endpoint.' : 'Unverified application write.');
  return route.abort('blockedbyclient');
}

// Each known tab gets its own socket policy; unknown workspace tabs remain guarded.
async function routeSocket(socket, state) {
    try { await assertPublicUrl(socket.url().replace(/^ws/, 'http'), state.allowLocal); }
    catch { return socket.close(); }
    if (state.normalNetworking) { socket.connectToServer();return; }
    if (!state.paused && (state.manual || ((state.source || state.research) && !FINAL_PATH.test(new URL(socket.url()).pathname)))) {
      const server = socket.connectToServer(), connection = {socket, server};
      state.sockets ||= new Set();state.sockets.add(connection);
      socket.onMessage(message => { if (!state.paused) server.send(message); });
      server.onMessage(message => { if (!state.paused) socket.send(message); });
      socket.onClose(() => { state.sockets.delete(connection);server.close().catch(() => {}); });
      server.onClose(() => { state.sockets.delete(connection);socket.close().catch(() => {}); });
    }
    else {
      const url = new URL(socket.url());
      state.blocked.push({method: 'WEBSOCKET', url: `${url.origin}${url.pathname}`, reason: state.source ? 'Possible final submission endpoint.' : 'Unverified WebSocket requires manual control.'});
      state.blocked = state.blocked.slice(-40);
      socket.close();
    }
}

// A popup or worker without an owned job tab receives the workspace's paused fallback.
function requestState(request, state) {
  if (!state.pages) return state;
  try { return state.pages.get(request.frame().page()) || state; }
  catch { return state; }
}

// CSP and same-origin page code remain outside the controller's trust boundary.
async function installGuards(context, state) {
  if (state.normalNetworking) throw new Error('Normal browsing guards must be installed on an owned page only.');
  state.guardInstalled = !(state.source && state.manual);
  if (state.guardInstalled) await context.addInitScript(browserGuard, false);
  await context.route('**/*', route => routeRequest(route, requestState(route.request(), state)));
  await context.routeWebSocket('**/*', socket => routeSocket(socket, state));
  const trackPage = page => page.on('domcontentloaded', async () => {
    const current = state.pages?.get(page) || state;
    if (!current.source && (current.manual || current.paused)) await page.evaluate(({manual, paused}) => {
      if (globalThis.__applyDesk) Object.assign(globalThis.__applyDesk, {manual, paused});
    }, {manual: !!current.manual, paused: !!current.paused}).catch(() => {});
  });
  context.pages().forEach(trackPage);
  context.on('page', trackPage);
}

// Only explicitly owned tabs receive identity, final-control checks and nonblocking ATS tracking.
async function installOwnedPageGuards(page, state) {
  if (!state.normalNetworking) throw new Error('An owned page requires normal networking mode.');
  if (state.ownedGuards) throw new Error('This owned page already has controller checks.');
  const restore = () => page.evaluate(({manual, paused}) => {
    if (globalThis.__applyDesk) Object.assign(globalThis.__applyDesk, {manual, paused});
  }, {manual: !!state.manual, paused: !!state.paused}).catch(() => {});
  const route = pending => routeRequest(pending, state);
  state.ownedGuards = {route, restore};
  state.ownedGuards.script = await page.addInitScript(browserGuard, {normalNetworking: true});
  await page.evaluate(browserGuard, {normalNetworking: true});
  await page.route('**/*', route);
  page.on('domcontentloaded', restore);
  await restore();
  state.guardInstalled = true;
}

// Disconnect releases only this controller's hooks while keeping the user's visible tab usable.
async function disposeOwnedPageGuards(page, state) {
  const guards = state.ownedGuards;
  if (!guards) return;
  state.ownedGuards = null;state.guardInstalled = false;
  page.removeListener('domcontentloaded', guards.restore);
  await page.unroute('**/*', guards.route).catch(() => {});
  await guards.script?.dispose().catch(() => {});
  await page.evaluate(() => {
    if (globalThis.__applyDesk) Object.assign(globalThis.__applyDesk, {manual: true, paused: false, finalUntil: 0});
  }).catch(() => {});
}

module.exports = {browserGuard, assertPublicUrl, installGuards, installOwnedPageGuards, disposeOwnedPageGuards, privateAddress, routeSocket};

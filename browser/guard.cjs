const dns = require('node:dns').promises;
const net = require('node:net');
const FINAL_PATH = /\/(submit|finalize|complete-application|send-application)(\/|$)/i;

// The page cannot submit forms or press Enter while automation owns the session.
function browserGuard(manual = false) {
  const state = {ids: new WeakMap(), elements: new Map(), counter: 0,
    documentId: [...crypto.getRandomValues(new Uint32Array(4))].map(value => value.toString(16)).join(''), manual, authUntil: 0, finalUntil: 0, finalId: null};
  globalThis.__applyDesk = state;
  const permitted = () => state.manual || Date.now() < state.authUntil;
  document.addEventListener('submit', event => {
    const final = Date.now() < state.finalUntil && state.elements.get(state.finalId) === event.submitter;
    if (!permitted() && !final) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('click', event => {
    const button = event.target.closest?.('button,input[type="submit"],[role="button"]');
    const label = button?.innerText || button?.value || button?.getAttribute('aria-label') || '';
    if (!state.manual && /submit|send application|finish|complete application/i.test(label) && !(Date.now() < state.finalUntil && state.elements.get(state.finalId) === button)) {
      event.preventDefault();event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('keydown', event => {
    if (!state.manual && event.key === 'Enter') { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  for (const name of ['submit', 'requestSubmit']) {
    const original = HTMLFormElement.prototype[name];
    HTMLFormElement.prototype[name] = function (...args) {
      const final = Date.now() < state.finalUntil && state.elements.get(state.finalId)?.form === this;
      if (permitted() || final) return original.apply(this, args);
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
  if (!state.blocked.some(item => JSON.stringify(item) === JSON.stringify(entry))) state.blocked.push(entry);
  state.blocked = state.blocked.slice(-40);
}

// Applications deny unverified writes; source pages render normally behind URL/final-path checks.
async function routeRequest(route, state) {
  const request = route.request();
  try { await assertPublicUrl(request.url(), state.allowLocal); }
  catch (error) { recordBlocked(state, request, error.message); return route.abort('blockedbyclient'); }
  if (state.manual) return route.continue();
  const url = new URL(request.url());
  const finalPath = FINAL_PATH.test(url.pathname);
  const mutation = !['GET', 'HEAD', 'OPTIONS'].includes(request.method());
  if (mutation && !finalPath && telemetry(url)) {
    recordBlocked(state, request, 'Unverified page telemetry.');return route.abort('blockedbyclient');
  }
  // Research has no account cookies, file uploads, form actions, or application commands.
  if (state.research && !finalPath) return route.continue();
  const permit = state.submission;
  if (permit && Date.now() < permit.expires && mutation && !permit.used && url.origin === permit.origin && (!permit.url || request.url() === permit.url)) {
    permit.used = true;return route.continue();
  }
  if (state.applicationSource && !state.source) return applicationRequest(route, state, finalPath, mutation);
  const auth = state.auth && Date.now() < state.auth.expires && request.method() === state.auth.method && request.url() === state.auth.url;
  if (!finalPath && (state.source || !mutation || auth)) return route.continue();
  recordBlocked(state, request, finalPath ? 'Possible final submission endpoint.' : 'Unverified mutation requires manual control.');
  return route.abort('blockedbyclient');
}

// Known analytics failures are not application failures; their requests remain blocked.
function telemetry(url) {
  const hosts = ['browser-intake-datadoghq.com', 'csp-report.browser-intake-datadoghq.com', 'sessions.bugsnag.com', 'analytics.google.com', 'www.google-analytics.com'];
  return hosts.includes(url.hostname) || (url.hostname === 'www.google.com' && url.pathname === '/g/collect');
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

// CSP and same-origin page code remain outside the controller's trust boundary.
async function installGuards(context, state) {
  state.guardInstalled = !(state.source && state.manual);
  if (state.guardInstalled) await context.addInitScript(browserGuard, false);
  await context.route('**/*', route => routeRequest(route, state));
  await context.routeWebSocket('**/*', async socket => {
    try { await assertPublicUrl(socket.url().replace(/^ws/, 'http'), state.allowLocal); }
    catch { return socket.close(); }
    if (state.manual || ((state.source || state.research) && !FINAL_PATH.test(new URL(socket.url()).pathname))) socket.connectToServer();
    else {
      const url = new URL(socket.url());
      state.blocked.push({method: 'WEBSOCKET', url: `${url.origin}${url.pathname}`, reason: state.source ? 'Possible final submission endpoint.' : 'Unverified WebSocket requires manual control.'});
      state.blocked = state.blocked.slice(-40);
      socket.close();
    }
  });
  const trackPage = page => page.on('domcontentloaded', async () => {
    if (!state.source && state.manual) await page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.manual = true; }).catch(() => {});
  });
  context.pages().forEach(trackPage);
  context.on('page', trackPage);
}

// Source pages keep normal networking while current and future documents block form submission.
async function armSource(context, state) {
  if (!['handshake', 'linkedin'].includes(state.source)) throw new Error('A recommendation source is required.');
  state.manual = false;
  state.auth = null;
  if (!state.guardInstalled) { await context.addInitScript(browserGuard, false); state.guardInstalled = true; }
  for (const page of context.pages()) {
    for (const frame of page.frames().filter(frame => !frame.isDetached())) {
      if (await frame.evaluate(browserGuard, false) !== true) throw new Error('The source document could not confirm its form-submission guard.');
    }
  }
}

module.exports = {assertPublicUrl, installGuards, privateAddress, armSource};

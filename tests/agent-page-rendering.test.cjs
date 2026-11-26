const assert = require('node:assert/strict');
const http = require('node:http');
const {chromium} = require('playwright');
const {installOwnedPageGuards} = require('../browser/guard.cjs');
const {waitAgentRendering} = require('../browser/agent-page-rendering.cjs');
const {dispatch} = require('../browser/worker.cjs');
const {revoke} = require('../browser/control.cjs');

// An unseen employer starts blank, paints a busy shell, then renders the actual role and form.
async function fixture() {
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(`<!doctype html><title>Employer application</title><body><script>
      setTimeout(()=>document.body.insertAdjacentHTML('beforeend','<main aria-busy="true"><h1>Careers</h1></main>'),150);
      setTimeout(()=>document.querySelector('main').innerHTML='<h1>Example Co — Hardware Graduate</h1><form><label>First name<input></label><button>Next</button></form>',500);
      setTimeout(()=>document.querySelector('main').setAttribute('aria-busy','false'),850);
    </script></body>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, url: `http://127.0.0.1:${server.address().port}/careers`};
}

// Worker open must return the delayed employer identity rather than an empty DOMContentLoaded snapshot.
async function testOpen(page, state, url) {
  const started = Date.now();
  const result = await dispatch({page, state, options: {headless: true}, context: page.context()}, 'open', {url, agent_driven: true});
  assert.match(result.text, /Example Co — Hardware Graduate/);
  assert(result.fields.some(field => field.label === 'First name'));
  assert(Date.now() - started >= 1900);
  assert(Date.now() - started < 8000);
}

// Busy or blank unknown pages return their actual evidence at the bound, without a fabricated page error.
async function testBound(page, state) {
  await page.setContent('<main aria-busy="true"><p>Employer returned an error.</p></main>');
  const result = await waitAgentRendering(page, state, {timeoutMs: 350, minimumMs: 100, quietMs: 50});
  assert.equal(result.timed_out, true);
  assert.match(await page.locator('body').innerText(), /Employer returned an error/);
  await page.setContent('');
  assert.equal((await waitAgentRendering(page, state, {timeoutMs: 200, minimumMs: 50, quietMs: 20})).timed_out, true);
  await page.setContent('<h1>Rendered role</h1><div aria-busy="true" hidden>Old spinner</div>');
  assert.equal((await waitAgentRendering(page, state, {timeoutMs: 500, minimumMs: 100, quietMs: 50})).timed_out, false);
}

// Stop revokes the poll promptly instead of waiting for the full render deadline.
async function testStop(page, state) {
  await page.setContent('<main aria-busy="true">Loading</main>');
  const started = Date.now(), pending = waitAgentRendering(page, state);
  const stop = setTimeout(() => revoke(state), 80);
  try { await assert.rejects(pending, /Application paused/); }
  finally { clearTimeout(stop); }
  assert(Date.now() - started < 500);
}

// Only isolated test pages are opened; no connected profile or employer is touched.
async function main() {
  const site = await fixture(), browser = await chromium.launch({headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page = await browser.newPage(), state = {workspace: true, normalNetworking: true, manual: false,
    paused: false, blocked: [], allowLocal: true, controller: new AbortController()};
  try {
    await installOwnedPageGuards(page, state);
    await testOpen(page, state, site.url);
    await testBound(page, state);
    await testStop(page, state);
    console.log('Generic delayed-SPA open, bounded actual-evidence fallback, hidden spinner and Stop passed.');
  } finally { await browser.close(); await new Promise(resolve => site.server.close(resolve)); }
}
main().catch(error => {console.error(error);process.exitCode = 1;});

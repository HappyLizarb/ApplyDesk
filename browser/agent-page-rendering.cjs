const {checkpoint} = require('./control.cjs');

// Startup readiness uses rendered evidence and visible busy indicators, never an employer-specific shell.
function renderedPageState() {
  const visible = element => getComputedStyle(element).visibility !== 'hidden' && !element.closest('[hidden],[inert]')
    && [...element.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
  const text = (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 24000);
  const headings = [...document.querySelectorAll('h1,[role="heading"]')].filter(visible).map(element => element.innerText.trim());
  const controls = [...document.querySelectorAll('input,select,textarea,button,[role="button"],[role="combobox"]')].filter(visible)
    .map(element => [element.tagName, element.type || element.getAttribute('role'), element.getAttribute('aria-label') || '', !!element.disabled]);
  return {fingerprint: JSON.stringify({url: location.href, text, headings, controls}),
    rendered: !!(text || headings.length || controls.length),
    busy: document.readyState === 'loading' || [...document.querySelectorAll('[aria-busy="true"]')].some(visible)};
}

// The bounded quiet period gives delayed SPA rendering time without turning an unknown page into an error.
async function waitAgentRendering(page, state, options = {}) {
  const check = checkpoint(state), started = Date.now();
  const timeout = options.timeoutMs ?? 8000, minimum = options.minimumMs ?? 2000, quiet = options.quietMs ?? 600;
  let previous = null, stableSince = started;
  while (Date.now() - started < timeout) {
    check();
    let current;
    try { current = await page.evaluate(renderedPageState); }
    catch (error) {
      check();
      if (!/Execution context was destroyed|Cannot find context/.test(error.message)) throw error;
    }
    check();
    const now = Date.now();
    if (!current || current.fingerprint !== previous || current.busy || !current.rendered) stableSince = now;
    previous = current?.fingerprint || null;
    if (current?.rendered && !current.busy && now - started >= minimum && now - stableSince >= quiet)
      return {timed_out: false, elapsed_ms: now - started};
    await page.waitForTimeout(Math.min(100, Math.max(1, timeout - (Date.now() - started))));
  }
  check();
  return {timed_out: true, elapsed_ms: Date.now() - started};
}

module.exports = {waitAgentRendering};

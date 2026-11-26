// Greenhouse's React controls expose labels and selections outside their native inputs.
function greenhouseField(element, clean) {
  if (!['job-boards.greenhouse.io', 'boards.greenhouse.io'].includes(location.hostname)) return {};
  const shell = element.closest('.select-shell');
  if (shell && element.getAttribute('role') === 'combobox') {
    return {supported: true, widget: 'greenhouse_select', value: greenhouseSelection(element)};
  }
  if (element.type === 'file') {
    let parent = element.parentElement;
    for (let depth = 0; parent && depth < 6; depth++, parent = parent.parentElement) {
      const heading = [...parent.querySelectorAll('label,legend,.label')].find(item => /^(Resume\/CV|Cover Letter)\s*\*?$/.test(clean(item.textContent)));
      if (heading) return {label: clean(heading.textContent), required: heading.textContent.includes('*'),
        ...(element.id === 'resume' ? {widget: 'greenhouse_upload'} : {})};
    }
  }
  return {};
}

// Country controls display a flag and dial code instead of their full selected label.
function greenhouseSelection(element) {
  const selected = element.closest('.select-shell')?.querySelector('.select__single-value');
  const flag = selected?.querySelector('.iti__flag')?.className.match(/\biti__([a-z]{2})\b/)?.[1];
  const country = flag ? new Intl.DisplayNames(['en'], {type: 'region'}).of(flag.toUpperCase()) + ' ' : '';
  return country + String(selected?.textContent || '').replace(/\s+/g, ' ').trim();
}

// Native validation proxies are not separate questions; keep unrelated hidden controls untouched.
function greenhouseProxy(element) {
  return ['job-boards.greenhouse.io', 'boards.greenhouse.io'].includes(location.hostname)
    && element.getAttribute('aria-hidden') === 'true' && element.tabIndex === -1
    && element.closest('.select-shell')?.querySelector('[role="combobox"]');
}

// A passive invisible-CAPTCHA badge has no task; a displayed challenge still blocks automation.
function passiveCaptchaBadge(element) {
  try {
    const url = new URL(element.src);
    return ['www.recaptcha.net', 'www.google.com'].includes(url.hostname)
      && /^\/recaptcha\/(enterprise|api2)\/anchor$/.test(url.pathname)
      && url.searchParams.get('size') === 'invisible';
  } catch { return false; }
}

// The embedded form declares its own POST destination; bind it to the visible tenant/job URL.
function greenhouseSubmission() {
  if (!['job-boards.greenhouse.io', 'boards.greenhouse.io'].includes(location.hostname)) return null;
  const current = new URL(location.href), tenant = current.searchParams.get('for'), job = current.searchParams.get('token');
  if (current.pathname !== '/embed/job_app' || !/^[a-zA-Z0-9_-]+$/.test(tenant || '') || !/^\d+$/.test(job || '')) return null;
  const raw = window.__remixContext?.state?.loaderData?.['routes/embed.job_app']?.submitPath;
  try {
    const target = new URL(raw);
    return target.protocol === 'https:' && target.hostname === 'boards.greenhouse.io' && !target.port
      && !target.username && !target.password && !target.search && !target.hash
      && target.pathname === `/embed/${tenant}/jobs/${job}` ? target.href : null;
  } catch { return null; }
}

// Select by an exact observed accessible label, then verify the committed display value.
async function selectGreenhouse(page, element, expected, check) {
  if (typeof expected !== 'string' || !expected.trim()) throw new Error('A dropdown needs a confirmed text answer.');
  const current = () => element.evaluate(greenhouseSelection);
  if ((await current()).toLowerCase() === expected.toLowerCase()) return expected;
  check();await element.click({signal: check.signal});
  await element.fill(expected, {signal: check.signal});
  const listId = await element.getAttribute('aria-controls');
  if (!listId) throw new Error('The dropdown has no associated option list.');
  const list = page.locator('[role="listbox"]').filter({has: page.getByRole('option')});
  const options = list.locator('[role="option"]').filter({hasText: new RegExp('^' + expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$', 'i')});
  await options.first().waitFor({state: 'visible', timeout: 10000});
  const ids = await options.evaluateAll(es => es.map(e => e.closest('[role="listbox"]')?.id));
  if (ids.length !== 1 || ids[0] !== listId) throw new Error('The dropdown answer is absent or ambiguous.');
  check();await options.click({signal: check.signal});
  if ((await current()).toLowerCase() !== expected.toLowerCase()) throw new Error('Dropdown selection did not match its confirmed answer.');
  return expected;
}

module.exports = {greenhouseField, greenhouseSelection, greenhouseProxy, passiveCaptchaBadge, greenhouseSubmission, selectGreenhouse};

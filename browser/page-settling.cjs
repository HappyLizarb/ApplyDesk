// Read after client rendering settles, while bounding pages with continuous background traffic.
async function settleResearchPage(page, {previousWorkdayStep, check} = {}) {
  check?.();await waitForPageIdle(page, check);check?.();
  const workday=/^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(new URL(page.url()).hostname)
    && /\/apply(?:\/|$)/.test(new URL(page.url()).pathname);
  let previous = '', stable = 0;
  for (let index = 0; index < (workday ? 60 : 12); index++) {
    await page.waitForTimeout(250);
    check?.();
    const text = await page.locator('body').innerText({timeout: 3000});
    stable = text.length >= 150 && text === previous ? stable + 1 : 0;
    const current = workday ? await page.evaluate(workdayFormState) : null;
    if (stable >= 2 && (!workday || current.terminal || workdayReady(current, previousWorkdayStep) && await workdayRequestsSettled(page))) return;
    previous = text;
  }
  const current = workday ? await page.evaluate(workdayFormState) : null;
  if (workday && !current.terminal && (!await workdayRequestsSettled(page) || !workdayReady(current, previousWorkdayStep))) throw new Error('The employer application form is still loading. No application was submitted.');
}

// Stop revokes the waiting action immediately while the employer's own requests keep running.
async function waitForPageIdle(page, check) {
  const idle = page.waitForLoadState('networkidle', {timeout: 10000}).catch(() => {});
  if (!check?.signal) return idle;
  let abort;
  const cancelled = new Promise((_, reject) => {
    abort = () => reject(check.signal.reason || new Error('Application paused.'));
    check.signal.addEventListener('abort', abort, {once: true});
    if (check.signal.aborted) abort();
  });
  try {await Promise.race([idle, cancelled]);}
  finally {check.signal.removeEventListener('abort', abort);}
}

// Only the original busy step must finish; a new form may legitimately disable its own Next button.
function workdayReady(current, previous) {
  if (current.terminal) return true;
  if (!current.rendered) return false;
  if (current.invalid) return true;
  const sameStep = !!previous?.step && current.step === previous.step;
  return !sameStep || !current.nextDisabled;
}

// A stable prior step can remain visible while Workday loads the next step's definitions or terms.
async function workdayRequestsSettled(page) {
  const origin = new URL(page.url()).origin, requests = await page.requests();
  return !requests.some(request => {
    const url = new URL(request.url());
    return url.origin === origin && /^\/wday\/(?:calypso\/)?cxs\//.test(url.pathname)
      && !request.failure() && request.timing().responseEnd < 0;
  });
}

// Workday's stable navigation shell is not evidence that its asynchronously loaded form is ready.
function workdayFormState() {
  const visible=node=>node.getClientRects().length && getComputedStyle(node).visibility!=='hidden';
  const terminal=/Something went wrong|application (?:has been )?submitted|Thank you for applying/i.test(document.body.innerText);
  const loading=[...document.querySelectorAll('[data-automation-id="applyFlowLoadingPage"]')].some(visible);
  const flow=document.querySelector('[data-automation-id="applyFlowPage"]');
  const steps=[...document.querySelectorAll('[data-automation-id^="applyFlow"][data-automation-id$="Page"]')]
    .filter(node=>node!==flow&&visible(node));
  const root=steps.length===1?steps[0]:flow?null:document;
  const hasInputs=root && [...root.querySelectorAll('input:not([type="hidden"]),textarea,select')].some(visible);
  const controls=[...document.querySelectorAll('button,a')].filter(visible).map(node=>node.innerText.trim());
  const hasEntry=controls.some(text=>/^(sign in with email|apply manually)$/i.test(text))
    || root && controls.some(text=>/^(submit application|submit)$/i.test(text));
  const state=globalThis.__applyDesk;
  if(root&&root!==document&&state&&!state.ids.has(root))state.ids.set(root,`${state.documentId}-${++state.counter}`);
  const step=root&&root!==document?`${state?.ids.get(root)||''}:${root.getAttribute('data-automation-id')}`:'';
  const next=[...document.querySelectorAll('[data-automation-id="pageFooter"] [data-automation-id="pageFooterNextButton"]')].filter(visible);
  const nextDisabled=next.length===1&&(next[0].disabled||next[0].getAttribute('aria-disabled')==='true');
  const invalid=!!root&&[...root.querySelectorAll('[aria-invalid="true"]')].some(visible);
  return {terminal,rendered:!loading&&!!(hasInputs||hasEntry),step,nextDisabled,invalid};
}

module.exports = {settleResearchPage,workdayFormState};

const {fresh} = require('./actions.cjs');
const {inspect} = require('./inspect.cjs');
const {assertPublicUrl} = require('./guard.cjs');

const FINAL = /^(submit application|send application|submit job application)$/i;
const DECLARATION = /\b(i (agree|accept|certify|attest|authorize|acknowledge|consent)|by (submitting|applying|clicking|continuing|proceeding|sending).{0,240}(agree|accept|certify|attest|authorize|acknowledge|consent)|terms of (service|use)|electronic signature)\b/is;
const RECEIPT = /\b(your application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|application submitted successfully|thank you for applying)\b/i;

// Opening a platform form never authorizes a write or a one-click submission.
async function applicationEntry(page, state, params) {
  const before = await fresh(page, state, params.snapshot_id);
  if (!state.applicationSource) throw new Error('A connected platform application session is required.');
  if (DECLARATION.test(before.text)) throw new Error('The application entry contains a declaration requiring your review.');
  const buttons = before.buttons.filter(button => /^(apply|apply now|apply externally|apply on handshake|easy apply)$/i.test(button.text) && !button.disabled);
  if (![params.company, params.title].every(value => typeof value === 'string' && value.trim() && buttons.every(button => button.context_text.toLowerCase().includes(value.toLowerCase())))) throw new Error('The application entry does not identify the expected employer and role.');
  const sameContext = buttons.length > 0 && buttons.every(button => button.context_id && button.context_id === buttons[0].context_id && button.text === buttons[0].text && !button.form_id);
  if (!sameContext && (buttons.length !== 1 || buttons[0].form_id)) throw new Error('A single non-submitting application entry was not found.');
  state.phase = 'entry';state.blocked = [];
  if (/externally/i.test(buttons[0].text)) return externalEntry(page, state, before, buttons[0]);
  const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), buttons[0].field_id);
  await element.asElement().click();
  await page.waitForTimeout(500);
  const after = await inspect(page, state);
  if (state.blocked.some(item => item.reason !== 'Unverified page telemetry.')) throw new Error('Opening the form attempted an unverified write. Continue manually.');
  state.phase = 'editing';
  return after;
}

// External destinations come from the clicked platform control, never model-generated URLs.
async function externalEntry(page, state, before, button) {
  const opened = [], listener = popup => opened.push(popup);
  page.on('popup', listener);
  try {
    const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), button.field_id);
    await element.asElement().click();
    const deadline = Date.now() + 7000;
    while (!opened.length && page.url() === before.url && Date.now() < deadline) await page.waitForTimeout(100);
    if (opened.length > 1) throw new Error('Multiple employer tabs opened; review the destination manually.');
    const target = opened[0] || page;
    await target.waitForLoadState('domcontentloaded', {timeout: 10000});
    const url = target.url();
    if (url === before.url || url === 'about:blank') throw new Error('The employer application link did not open.');
    await assertPublicUrl(url, state.allowLocal);
    if (state.blocked.some(item => item.reason !== 'Unverified page telemetry.')) throw new Error('Opening the employer page attempted an unverified write.');
    return {...before, external_application_url: url};
  } finally { page.removeListener('popup', listener); }
}

// The controller supplies an audited exact snapshot, never a generic click instruction.
async function submitApplication(page, state, params) {
  const before = await fresh(page, state, params.snapshot_id);
  const buttons = before.buttons.filter(button => FINAL.test(button.text) && !button.disabled);
  if (buttons.length !== 1 || buttons[0].field_id !== params.field_id || !params.attempt_id) throw new Error('The audited submission control changed.');
  if (state.submissionAttempt) throw new Error('This browser already attempted submission. Do not retry an uncertain result.');
  if (DECLARATION.test(before.text) || before.warnings.length || before.guard.blocked_requests.length) throw new Error('The final page needs manual review.');
  if (![params.company, params.title].every(value => typeof value === 'string' && value.trim() && before.text.toLowerCase().includes(value.toLowerCase()))) throw new Error('The final page does not identify the expected employer and role.');
  state.submissionAttempt = params.attempt_id;
  const button = buttons[0], origin = new URL(before.url).origin;
  state.submission = {origin, expires: Date.now() + 10000, used: false, url: button.form_action || null};
  await page.evaluate(({id, expires}) => { globalThis.__applyDesk.finalId = id;globalThis.__applyDesk.finalUntil = expires; }, {id: button.field_id, expires: state.submission.expires});
  try {
    const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), button.field_id);
    await element.asElement().click({timeout: 5000});
    return await confirmation(page, state, before, params.attempt_id);
  } finally {
    state.submission = null;
    await page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.finalUntil = 0; }).catch(() => {});
  }
}

// A click or HTTP success is insufficient: require a newly visible receipt and no live final form.
async function confirmation(page, state, before, attemptId) {
  const deadline = Date.now() + 10000;
  let after;
  do {
    await page.waitForTimeout(250);
    after = await inspect(page, state).catch(() => null);
    const receipt = after?.text.match(RECEIPT)?.[0];
    if (receipt && !before.text.includes(receipt) && !after.buttons.some(button => FINAL.test(button.text) && !button.disabled)) {
      return {confirmed: true, attempt_id: attemptId, url: after.url, receipt};
    }
  } while (Date.now() < deadline);
  return {confirmed: false, attempt_id: attemptId, url: after?.url || before.url, receipt: ''};
}

module.exports = {applicationEntry, submitApplication, DECLARATION};

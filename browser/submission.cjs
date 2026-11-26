const {watchAshbyReceipt} = require('./ashby-submission.cjs');
const {employerEntry} = require('./employer-entry.cjs');
const {fresh} = require('./actions.cjs');
const {inspect} = require('./inspect.cjs');
const {assertPublicUrl} = require('./guard.cjs');
const {checkpoint} = require('./control.cjs');

const FINAL = /^(submit application|send application|submit job application)$/i;
const DECLARATION = /\b(i (agree|accept|certify|attest|authorize|acknowledge|consent)|by (submitting|applying|clicking|continuing|proceeding|sending).{0,240}(agree|accept|certify|attest|authorize|acknowledge|consent)|terms of (service|use)|electronic signature)\b/is;
// Required employer terms are part of applying. Keep independent declarations in the review.
function needsDeclarationReview(text) {
  const terms = /\b(terms|agreement|arbitration|waiver|privacy|notice|policy|screening|background|reference checks?|accuracy|accurate|truthful|electronic signature)\b/i;
  const separate = /\b(optional|marketing|newsletter|recording|transcription|citizen\w*|clearance|sponsorship|relatives?)\b/i;
  return text.split(/\n/).some(line => DECLARATION.test(line) && (separate.test(line) || !terms.test(line)));
}

const RECEIPT = /\b(your application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|application submitted successfully|thank you for applying)\b/i;

// Opening a platform form never authorizes a write or a one-click submission.
async function applicationEntry(page, state, params) {
  if (!state.applicationSource && params.link_id) return employerEntry(page,state,params);
  const check = checkpoint(state);
  const before = await fresh(page, state, params.snapshot_id);
  if (!state.applicationSource) throw new Error('A connected platform application session is required.');
  if (needsDeclarationReview(before.declaration_text ?? before.text)) throw new Error('The application entry contains a declaration requiring your review.');
  const buttons = before.buttons.filter(button => /^(apply|apply now|apply externally|apply on handshake|easy apply)$/i.test(button.text) && !button.disabled);
  if (![params.company, params.title].every(value => typeof value === 'string' && value.trim() && buttons.every(button => button.context_text.toLowerCase().includes(value.toLowerCase())))) throw new Error('The application entry does not identify the expected employer and role.');
  const sameContext = buttons.length > 0 && buttons.every(button => button.context_id && button.context_id === buttons[0].context_id && button.text === buttons[0].text && !button.form_id);
  if (!sameContext && (buttons.length !== 1 || buttons[0].form_id)) throw new Error('A single non-submitting application entry was not found.');
  check();state.phase = 'entry';state.blocked = [];
  if (/externally/i.test(buttons[0].text)) return externalEntry(page, state, before, buttons[0]);
  const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), buttons[0].field_id);
  check();await element.asElement().click({signal: check.signal});
  await page.waitForTimeout(500);
  check();
  const after = await inspect(page, state);
  if (state.blocked.some(item => item.reason !== 'Unverified page telemetry.')) throw new Error('Opening the form attempted an unverified write. Continue manually.');
  state.phase = 'editing';
  return after;
}

// External destinations come from the clicked platform control, never model-generated URLs.
async function externalEntry(page, state, before, button) {
  const check = checkpoint(state);
  const opened = [], listener = popup => opened.push(popup);
  page.on('popup', listener);
  try {
    const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), button.field_id);
    check();await element.asElement().click({signal: check.signal});
    const deadline = Date.now() + 7000;
    while (!opened.length && page.url() === before.url && Date.now() < deadline) { await page.waitForTimeout(100);check(); }
    if (opened.length > 1) throw new Error('Multiple employer tabs opened; review the destination manually.');
    const target = opened[0] || page;
    await target.waitForLoadState('domcontentloaded', {timeout: 10000});
    const url = target.url();
    if (url === before.url || url === 'about:blank') throw new Error('The employer application link did not open.');
    await assertPublicUrl(url, state.allowLocal);
    check();
    if (state.blocked.some(item => item.reason !== 'Unverified page telemetry.')) throw new Error('Opening the employer page attempted an unverified write.');
    return {...before, external_application_url: url};
  } finally {
    page.removeListener('popup', listener);
    if (state.workspace) await Promise.all(opened.map(popup => popup.close().catch(() => {})));
  }
}

// The controller supplies an audited exact snapshot, never a generic click instruction.
async function submitApplication(page, state, params) {
  const check = checkpoint(state);
  const before = await fresh(page, state, params.snapshot_id);
  const buttons = before.buttons.filter(button => FINAL.test(button.text) && !button.disabled);
  if (buttons.length !== 1 || buttons[0].field_id !== params.field_id || !params.attempt_id) throw new Error('The audited submission control changed.');
  const rejected = params.retry_verification === true && state.submissionAttempt === params.attempt_id && state.verificationResult?.issue === 'Incorrect security code';
  if (state.submissionAttempt && !rejected) throw new Error('This browser already attempted submission. Do not retry an uncertain result.');
  if (needsDeclarationReview(before.declaration_text ?? before.text) || before.warnings.length || before.guard.blocked_requests.length) throw new Error('The final page needs manual review.');
  if (![params.company, params.title].every(value => typeof value === 'string' && value.trim() && before.text.toLowerCase().includes(value.toLowerCase()))) throw new Error('The final page does not identify the expected employer and role.');
  check();state.submissionAttempt = params.attempt_id;state.verificationResult = null;
  if (rejected) { state.verificationContinuationUsed = false;state.verificationCodeHash = null; }
  const button = buttons[0], target = before.submission_target || button.form_action || before.url;
  await assertPublicUrl(target, state.allowLocal);
  state.submission = {origin: new URL(target).origin, expires: Date.now() + 30000, used: false,
    url: before.submission_target || button.form_action || null, greenhouse: !!before.submission_target && before.submission_provider !== 'ashby', ashby: before.ashby_submission};
  watchAshbyReceipt(page,state.submission);
  await page.evaluate(({id, expires}) => { globalThis.__applyDesk.finalId = id;globalThis.__applyDesk.finalUntil = expires; }, {id: button.field_id, expires: state.submission.expires});
  try {
    const element = await page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), button.field_id);
    check();await element.asElement().click({timeout: 5000, signal: check.signal});
    const result = await confirmation(page, state, before, params.attempt_id);
    state.verificationResult = result;
    return result;
  } finally {
    state.submission?.cleanup?.();
    state.submission = null;
    await page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.finalUntil = 0; }).catch(() => {});
  }
}

// A click or HTTP success is insufficient: require a newly visible receipt and no live final form.
async function confirmation(page, state, before, attemptId) {
  const deadline = Date.now() + 30000;
  let after;
  do {
    await page.waitForTimeout(250);
    after = await inspect(page, state).catch(() => null);
    const verification = after?.text.match(/A verification code was sent to [^\n]+/i)?.[0];
    if (verification && after.fields.some(field => /^security code$/i.test(field.label)) && !before.text.includes(verification)) {
      return {confirmed: false, verification_required: true, attempt_id: attemptId, url: after.url, receipt: '', issue: verification};
    }
    if (state.submission?.receipt && after && !after.buttons.some(button=>FINAL.test(button.text) && !button.disabled)) {
      return {confirmed:true, attempt_id:attemptId, url:after.url, receipt:state.submission.receipt, surveys_confirmed:state.submission.surveys_confirmed};
    }
    const receipt = after?.text.match(RECEIPT)?.[0];
    if (receipt && !before.text.includes(receipt) && !after.buttons.some(button => FINAL.test(button.text) && !button.disabled)) {
      return {confirmed: true, attempt_id: attemptId, url: after.url, receipt};
    }
  } while (Date.now() < deadline);
  return {confirmed: false, attempt_id: attemptId, url: after?.url || before.url, receipt: ''};
}

module.exports = {applicationEntry, submitApplication, DECLARATION, needsDeclarationReview};

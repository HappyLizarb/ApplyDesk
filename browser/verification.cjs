const crypto = require('node:crypto');
const {inspect} = require('./inspect.cjs');
const {fill} = require('./actions.cjs');
const {checkpoint, pauseWorkspace} = require('./control.cjs');
const {assertPublicUrl} = require('./guard.cjs');
const {DECLARATION} = require('./submission.cjs');

// An explicit verification command resumes only its audited pending challenge on the original tab.
async function continueVerification(session, authorization, code) {
  const generation = session.generation || 0;
  const tab = session.tabs.get(authorization.job_id);
  if (!tab || tab.page.isClosed() || tab.state.submissionAttempt !== authorization.attempt_id) throw Error('The original pending attempt is unavailable.');
  if ((tab.state.verificationContinuationUsed && tab.state.verificationResult?.issue !== 'Incorrect security code') || !/^[a-z0-9]{8}$/i.test(code)) throw Error('Verification was already attempted or the code is malformed.');
  const digest = crypto.createHash('sha256').update(code).digest('hex');
  if (tab.state.verificationCodeHash === digest) throw Error('This code was already attempted. Supply a new code.');
  await session.pausePromise;
  const before = await inspect(tab.page, tab.state);
  validateChallenge(before, authorization);
  if ((session.generation || 0) !== generation) throw Error('Application was stopped.');
  tab.state.generation += 1;
  Object.assign(tab.state, {paused: false, manual: false, controller: new AbortController(), submission: null});
  try {
    await tab.page.evaluate(() => { Object.assign(globalThis.__applyDesk, {paused: false, manual: false, finalUntil: 0, finalId: null}); });
    const check = checkpoint(tab.state);check();
    await tab.page.bringToFront();
    const after = await fillCode(tab, before, authorization, code, check);
    tab.state.verificationCodeHash = digest;
    const result = await sendVerified(tab, after, authorization, check);
    tab.state.verificationResult = result;
    return result;
  } finally { await pauseWorkspace(session, authorization.job_id); }
}

// Same document, employer, audited snapshot, challenge group and exact ATS destination are required.
function validateChallenge(page, authorization) {
  if (page.snapshot_id !== authorization.snapshot.snapshot_id || page.warnings.length || page.guard.blocked_requests.length || DECLARATION.test(page.text)) throw Error('The audited application changed or needs review.');
  const url = new URL(page.url), target = new URL(page.submission_target);
  if (url.origin !== 'https://job-boards.greenhouse.io' || url.pathname !== '/embed/job_app'
      || target.href !== `https://boards.greenhouse.io/embed/${url.searchParams.get('for')}/jobs/${url.searchParams.get('token')}`) throw Error('The original employer destination changed.');
  if (![authorization.company, authorization.title].every(value => value && page.text.toLowerCase().includes(value.toLowerCase()))) throw Error('Employer or job identity changed.');
  const fields = page.fields.filter(field => authorization.code_ids.includes(field.field_id));
  if (fields.length !== 8 || fields[0].label !== 'Security code' || new Set(fields.map(field => field.group_id)).size !== 1
      || fields.some(field => field.type !== 'text' || !field.required || !/A verification code was sent to/.test(field.group))) throw Error('Email verification fields changed.');
}

// All application fields and attached bytes must remain identical while only code cells change.
async function fillCode(tab, before, authorization, code, check) {
  const stable = snapshot => JSON.stringify(snapshot.fields.filter(field => !authorization.code_ids.includes(field.field_id)));
  let current = before;
  for (const [index, id] of authorization.code_ids.entries()) {
    check();current = await fill(tab.page, tab.state, {snapshot_id: current.snapshot_id, answers: [{field_id: id, value: code[index]}]});
    if (stable(current) !== stable(before) || current.warnings.length || current.guard.blocked_requests.length) throw Error('The application changed during verification.');
  }
  if (authorization.code_ids.map(id => current.fields.find(field => field.field_id === id)?.value).join('') !== code) throw Error('Verification code readback failed.');
  return current;
}

// One continuation of the durable attempt gets one exact POST; no duplicate-attempt state is cleared.
async function sendVerified(tab, before, authorization, check) {
  const buttons = before.buttons.filter(button => /^submit application$/i.test(button.text) && !button.disabled);
  if (buttons.length !== 1 || before.submission_target !== authorization.snapshot.submission_target || DECLARATION.test(before.text)) throw Error('The final submission control changed.');
  const target = await assertPublicUrl(before.submission_target, tab.state.allowLocal);
  check();tab.state.verificationContinuationUsed = true;
  tab.state.submission = {origin: target.origin, url: target.href, greenhouse: true, expires: Date.now() + 30000, used: false};
  await tab.page.evaluate(({id, expires}) => { Object.assign(globalThis.__applyDesk, {finalId: id, finalUntil: expires}); }, {id: buttons[0].field_id, expires: tab.state.submission.expires});
  try {
    const handle = await tab.page.evaluateHandle(id => globalThis.__applyDesk.elements.get(id), buttons[0].field_id);
    check();await handle.asElement().click({timeout: 5000, signal: check.signal});
    return await receipt(tab, authorization.attempt_id, check, before.text);
  } finally {
    tab.state.submission = null;
    await tab.page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.finalUntil = 0; }).catch(() => {});
  }
}

// Only an employer receipt with no active final form is success; invalid codes remain Needs input.
async function receipt(tab, attemptId, check, previousText) {
  const deadline = Date.now() + 30000;
  let page;
  do {
    await tab.page.waitForTimeout(300);check();
    page = await inspect(tab.page, tab.state).catch(() => null);
    const message = page?.text.match(/your application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|application submitted successfully|thank you for applying/i)?.[0];
    if (message && !previousText.includes(message) && !page.buttons.some(button => /^submit application$/i.test(button.text) && !button.disabled)) return {confirmed: true, attempt_id: attemptId, url: page.url, receipt: message};
    const invalid = page?.text.match(/[^\n]*(?:invalid|incorrect|expired)[^\n]*(?:code|verification)[^\n]*|[^\n]*(?:code|verification)[^\n]*(?:invalid|incorrect|expired)[^\n]*/i)?.[0];
    if (invalid) return {confirmed: false, verification_required: true, attempt_id: attemptId, url: page.url, receipt: '', issue: invalid};
  } while (Date.now() < deadline);
  return {confirmed: false, attempt_id: attemptId, url: page?.url, receipt: '', issue: 'No employer confirmation received.', blocked: page?.guard.blocked_requests};
}

module.exports = {continueVerification, validateChallenge};

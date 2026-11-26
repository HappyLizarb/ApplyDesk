const {agentObserve, agentPageCheck, agentElement} = require('./agent-page.cjs');
const {assertPublicUrl} = require('./guard.cjs');

const AUTH = /\b(sign[ -]?in|log[ -]?in|create (?:an? )?account|register|password)\b/i;
const {needsDeclarationReview} = require('./submission.cjs');
const RECEIPT = /\b(your application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|application (?:was |has been )?(?:successfully submitted|submitted successfully)|thank you for applying)\b/i;

// The configured agent chooses the final control; the controller binds that choice to its exact reviewed page.
function reviewedControl(before, params) {
  if (before.snapshot_id !== params.snapshot_id) throw new Error('The application changed after review. Observe it again.');
  const final = before.elements.find(element => element.id === params.final_element_id);
  const button = final?.tag === 'button' || (final?.tag === 'input' && ['button', 'submit'].includes(final.type)) || final?.role === 'button';
  if (!button || !final.visible || final.disabled || (!final.form_id && !final.context))
    throw new Error('The reviewed final application control is unavailable.');
  if (final.authentication_form || AUTH.test(final.label))
    throw new Error('Authentication cannot be treated as a job submission.');
  if (!/^[a-f0-9]{64}$/.test(params.review_digest || '') || !params.attempt_id)
    throw new Error('The application has no reserved reviewed submission attempt.');
  if (before.loading || invalidFinalAnswer(before, final) || before.text_truncated || before.elements_truncated || hasDeclaration(before, final))
    throw new Error('The final page needs review before submission.');
  for (const key of ['company', 'title']) {
    const quote = params[`${key}_quote`], expected = params[key];
    if (typeof quote !== 'string' || !quote.trim() || !before.text.includes(quote)
        || typeof expected !== 'string' || !expected.trim() || !quote.toLowerCase().includes(expected.toLowerCase()))
      throw new Error('The reviewed company or job title is no longer visible.');
  }
  if (typeof params.resume_quote !== 'string' || !params.resume_quote.trim() || !before.text.includes(params.resume_quote))
    throw new Error('The reviewed resume evidence is no longer visible.');
  return final;
}

// Recheck required choices immediately before dispatch against the reviewed snapshot.
function hasDeclaration(before, final) {
  return needsDeclarationReview(before.text) || before.elements.some(element =>
    (!final.form_id || element.form_id === final.form_id) && element.type === 'checkbox' && element.required
    && !element.disabled && ![true, 'true'].includes(element.checked));
}

// Other page forms and informational status alerts do not invalidate the selected application form.
function invalidFinalAnswer(before, final) {
  const ids = new Set(before.elements.filter(item => !final.form_id || item.form_id === final.form_id).map(item => item.id));
  return before.errors.some(item => item.invalid && ids.has(item.id));
}

// A durable Python reservation is consumed once, even if the browser loses the employer's response.
async function agentSubmit(page, state, params) {
  const check = agentPageCheck(state), before = await agentObserve(page, state);
  const final = reviewedControl(before, params);
  const rejected = params.retry_verification === true && state.submissionAttempt === params.attempt_id
    && state.verificationResult?.issue === 'Incorrect security code';
  if (state.submissionAttempt && !rejected) throw new Error('This browser already attempted submission; review the receipt before retrying.');
  await assertPublicUrl(final.form_action || before.url, state.allowLocal);
  const element = await agentElement(page, final.id);
  check();
  state.submissionAttempt = params.attempt_id;
  state.verificationResult = null;
  try {
    await page.evaluate(({id, expires}) => {
      globalThis.__applyDesk.finalId = id;
      globalThis.__applyDesk.finalUntil = expires;
    }, {id: final.id, expires: Date.now() + 30000});
    check();
    await element.click({timeout: 5000, signal: check.signal});
    const result = await agentConfirmation(page, state, before, final, params.attempt_id);
    state.verificationResult = result;
    return result;
  } finally {
    await page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.finalUntil = 0; }).catch(() => {});
  }
}

// Require newly visible employer confirmation and disappearance of the active application form.
async function agentConfirmation(page, state, before, final, attemptId) {
  const check = agentPageCheck(state), deadline = Date.now() + 30000;
  let after;
  do {
    await page.waitForTimeout(250);
    check();
    after = await agentObserve(page, state).catch(() => null);
    if (!after || after.loading) continue;
    const verification = after.text.match(/(?:a |the )?verification code was sent to [^\n]+/i)?.[0];
    if (verification && !before.text.includes(verification) && after.elements.some(item => /(?:security|verification) code/i.test(item.label)))
      return {confirmed: false, verification_required: true, attempt_id: attemptId, url: after.url, receipt: '', issue: verification};
    const receipt = after.text.match(RECEIPT)?.[0];
    const stillPresent = after.elements.some(item => item.visible && !item.disabled
      && (item.id === final.id || ((item.tag === 'button' || item.role === 'button' || item.type === 'submit')
        && (item.label || item.text) === (final.label || final.text))));
    if (receipt && !before.text.includes(receipt) && !stillPresent && !after.errors.some(item => item.invalid))
      return {confirmed: true, attempt_id: attemptId, url: after.url, receipt};
  } while (Date.now() < deadline);
  return {confirmed: false, attempt_id: attemptId, url: after?.url || before.url, receipt: '',
    issue: after?.errors.map(item => item.text).filter(Boolean).join('; ') || 'No new employer receipt was observed.'};
}

module.exports = {agentSubmit, reviewedControl};

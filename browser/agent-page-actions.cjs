const {agentObserve, agentPageCheck, agentElement} = require('./agent-page.cjs');
const {assertPublicUrl} = require('./guard.cjs');
const {agentUpload} = require('./agent-page-upload.cjs');

const FINAL = /\b(submit|send application|finish|complete application|confirm application|withdraw|delete account|accept offer|purchase|pay now)\b/i;

// The model chooses a current observed control; final submission remains a different operation.
async function agentAction(page, state, params) {
  const check = agentPageCheck(state), before = await agentObserve(page, state);
  if (!params.snapshot_id || params.snapshot_id !== before.snapshot_id)
    return {...before, action_status: 'stale', action_error: 'The page changed. Choose from this observation.'};
  if (!['explore', 'advance', 'authentication'].includes(params.intent)) throw new Error('Declare the action intent.');
  const field = before.elements.find(item => item.id === params.element_id);
  if (!['wait', 'back'].includes(params.action) && !field) throw new Error('Choose an element from the current observation.');
  if (field?.disabled && !['scroll', 'wait'].includes(params.action)) throw new Error('The observed control is disabled.');
  if (field?.type === 'password' && params.action !== 'scroll') throw new Error('Password fields require the protected account backend.');
  let result;
  try {
    result = await performAgentAction(page, state, params, field, check);
    await page.waitForTimeout(params.action === 'wait' ? 0 : 150);
    check();
  } catch (error) {
    check();
    if (error.name !== 'TimeoutError' && error.code !== 'AGENT_STALE_ELEMENT' && !/Element is not attached to the DOM/.test(error.message)) throw error;
    return {...await agentObserve(page, state), action_status: 'uncertain', action_error: String(error.message).split('Call log:')[0].slice(0, 300)};
  }
  return {...await agentObserve(page, state), action_status: 'performed', ...(result ? {result} : {})};
}

// Navigation and waits stay bounded and use the browser's real page rather than synthesized DOM writes.
async function performAgentAction(page, state, params, field, check) {
  if (params.action === 'wait') {
    const duration = Math.max(0, Math.min(3000, Number(params.value) || 500));
    await page.waitForTimeout(duration); check(); return;
  }
  if (params.action === 'back') {
    if (!state.agentPreviousUrl) throw new Error('No previously observed page is available.');
    await assertPublicUrl(state.agentPreviousUrl, state.allowLocal);
    check(); await page.goto(state.agentPreviousUrl, {waitUntil: 'domcontentloaded', timeout: 10000}); return;
  }
  if (params.action === 'upload') return {upload: await agentUpload(page, state, field, params.upload, check)};
  const element = await agentElement(page, field.id);
  if (params.action === 'scroll') { check(); await element.scrollIntoViewIfNeeded({timeout: 3000, signal: check.signal}); return; }
  if (params.action === 'click') return agentClick(page, state, element, field, params.intent, check);
  if (params.action === 'fill') return agentFill(element, field, params.value, check);
  if (params.action === 'select') return agentSelect(element, field, params.value, check);
  if (params.action === 'check') return agentCheckControl(element, field, params.value, check);
  throw new Error('Unknown agent page action.');
}

// Native submit buttons require declared progression; final or unrelated irreversible controls stay reserved.
async function agentClick(page, state, element, field, intent, check) {
  const accountSubmit = /\b(sign in|log in|login|register|sign up|create account)\b/i.test(field.label);
  if (field.authentication_form && (field.type === 'submit' || accountSubmit || intent !== 'explore'))
    throw new Error('Authentication submits require the protected account backend.');
  if (intent === 'authentication') throw new Error('Authentication requires the protected account backend.');
  if (FINAL.test(field.label)) throw new Error('Use the separate reviewed submission operation for this control.');
  if (field.type === 'submit' && intent !== 'advance')
    throw new Error('A native submit button requires an explicit reviewed advance or final submission decision.');
  if (field.download) throw new Error('Downloads are not application navigation.');
  if (field.href) await assertPublicUrl(field.href, state.allowLocal);
  check(); await element.click({timeout: 7000, signal: check.signal});
}

// Fill uses native input events and independently reads the resulting value.
async function agentFill(element, field, value, check) {
  if (field.readonly || typeof value !== 'string' || value.length > 12000 || ['checkbox','radio','file','submit'].includes(field.type))
    throw new Error('Fill requires an editable observed text control and bounded text.');
  check(); await element.fill(value, {timeout: 7000, signal: check.signal});
  const actual = await element.evaluate(item => item.isContentEditable ? item.innerText : item.value);
  if (actual !== value) throw new Error('The field did not retain the intended value.');
  return {readback: {element_id: field.id, value: actual, matches: true}};
}

// Native options use exact observed values; custom options are clicked by their observed IDs.
async function agentSelect(element, field, value, check) {
  const values = Array.isArray(value) ? value : [value];
  if (field.tag !== 'select' || (!field.multiple && values.length !== 1) || !values.length
      || values.some(value => typeof value !== 'string' || field.options.filter(option => option.value === value && !option.disabled).length !== 1))
    throw new Error('Select requires exact enabled options from the current observation.');
  check(); await element.selectOption(values, {timeout: 7000, signal: check.signal});
  const actual = await element.evaluate(item => [...item.selectedOptions].map(option => option.value));
  if (JSON.stringify([...actual].sort()) !== JSON.stringify([...values].sort())) throw new Error('The selected options did not match.');
  return {readback: {element_id: field.id, value: field.multiple ? actual : actual[0], matches: true}};
}

// Native and ARIA check controls report real checked state instead of assuming a click succeeded.
async function agentCheckControl(element, field, value, check) {
  if (typeof value !== 'boolean' || !['checkbox','radio'].includes(field.type) && !['checkbox','radio'].includes(field.role)
      || ((field.type === 'radio' || field.role === 'radio') && !value)) throw new Error('Check requires an observed checkbox/radio and explicit boolean.');
  check();
  if (['checkbox','radio'].includes(field.type)) await element.setChecked(value, {timeout: 7000, signal: check.signal});
  else if ((field.checked === 'true') !== value) await element.click({timeout: 7000, signal: check.signal});
  const actual = await element.evaluate(item => ['checkbox','radio'].includes(item.type) ? item.checked : item.getAttribute('aria-checked') === 'true');
  if (actual !== value) throw new Error('The selected state did not match.');
  return {readback: {element_id: field.id, value: actual, matches: true}};
}

module.exports = {agentAction};

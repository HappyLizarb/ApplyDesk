const {agentObserve, agentElement, agentPageCheck} = require('./agent-page.cjs');

// Only the backend can provide credentials; this private operation is absent from the model action schema.
async function agentAuthenticate(page, state, params) {
  const check = agentPageCheck(state), snapshot = await agentObserve(page, state);
  if (snapshot.snapshot_id !== params.snapshot_id) throw new Error('The authentication form changed. Observe it again.');
  const context = authenticationContext(snapshot, params, state.allowLocal);
  state.agentRedactions = [...new Set([...(state.agentRedactions || []), params.password])];
  try {
    await verifyAuthenticationContext(page, context);
    const username = await agentElement(page, params.username_id);
    check(); await username.fill(params.username, {timeout: 7000, signal: check.signal});
    for (const id of params.password_ids) {
      await verifyAuthenticationContext(page, context);
      const password = await agentElement(page, id);
      check(); await password.fill(params.password, {timeout: 7000, signal: check.signal});
      if (await password.inputValue() !== params.password) throw new Error('Credential readback failed.');
    }
    await verifyAuthenticationContext(page, context);
    if (await username.inputValue() !== params.username) throw new Error('Account readback failed.');
    const button = await agentElement(page, params.submit_id);
    await installAuthenticationGate(button);
    check(); await button.click({timeout: 10000, signal: check.signal});
    await page.waitForTimeout(250);check();
    return {...await agentObserve(page, state), action_status: 'performed', authentication_attempted: true};
  } catch (error) {
    check();throw new Error('Authentication could not be confirmed. Observe the account page before retrying.');
  } finally {
    await page.evaluate(() => {
      const state = globalThis.__applyDesk, guard = state?.agentAuthGuard;
      if (guard) {
        guard.form.removeEventListener('submit', guard.preventGet, true);
        if (guard.submitDescriptor) Object.defineProperty(guard.form, 'submit', guard.submitDescriptor); else delete guard.form.submit;
      }
      if (state) Object.assign(state, {authTarget: null, authUntil: 0, agentAuthGuard: null});
    }).catch(() => {});
  }
}

// The selected identity, every password control and submit button must share one current HTTPS form.
function authenticationContext(snapshot, params, allowLocal) {
  if (!['sign_in','create_account'].includes(params.intent) || typeof params.username !== 'string' || !params.username
      || typeof params.password !== 'string' || !params.password || !Array.isArray(params.password_ids)
      || !params.password_ids.length || params.password_ids.length > 2 || new Set(params.password_ids).size !== params.password_ids.length)
    throw new Error('Authentication requires one backend-selected account.');
  const username = snapshot.elements.find(item => item.id === params.username_id);
  const button = snapshot.elements.find(item => item.id === params.submit_id);
  const form = username?.form_id, fields = snapshot.elements.filter(item => item.form_id === form);
  const passwords = fields.filter(item => item.type === 'password');
  const url = new URL(snapshot.url), action = new URL(username?.form_action || snapshot.url);
  if (!form || !['text','email'].includes(username.type) || username.disabled || username.readonly
      || button?.form_id !== form || button.disabled || !button.authentication_form
      || /\b(submit application|complete application|withdraw|delete|purchase|pay now|accept offer)\b/i.test(button.label)
      || !(button.tag === 'button' || button.role === 'button' || ['submit','button'].includes(button.type))
      || action.origin !== url.origin || (!allowLocal && action.protocol !== 'https:')
      || passwords.length !== params.password_ids.length || passwords.some(item => item.disabled || item.readonly || !params.password_ids.includes(item.id))
      || fields.some(item => item.form_action !== username.form_action))
    throw new Error('The authentication controls do not share one verified account form.');
  return {url: snapshot.url, form, action: username.form_action, username: params.username_id,
    passwords: params.password_ids, button: params.submit_id};
}

// Recheck DOM ownership directly before each secret write and before the final authentication click.
async function verifyAuthenticationContext(page, context) {
  const valid = await page.evaluate(context => {
    const elements = globalThis.__applyDesk?.elements, form = elements?.get(context.form);
    const selected = [context.username, ...context.passwords, context.button].map(id => elements?.get(id));
    const passwords = [...form?.querySelectorAll('input[type="password"]') || []];
    return location.href === context.url && form?.isConnected && form.action === context.action
      && selected.every(element => element?.isConnected && (element.form || element.closest('form')) === form && !element.disabled)
      && passwords.length === context.passwords.length && passwords.every(element => context.passwords.some(id => elements.get(id) === element));
  }, context);
  if (!valid) throw new Error('The authentication form changed.');
}

// Default-GET SPA forms may run click/fetch handlers, but must never navigate with a password in the URL.
async function installAuthenticationGate(button) {
  await button.evaluate(target => {
    const state = globalThis.__applyDesk, form = target.form || target.closest('form');
    const preventGet = event => { if (form.method.toUpperCase() !== 'POST') event.preventDefault(); };
    const submitDescriptor = Object.getOwnPropertyDescriptor(form, 'submit'), nativeSubmit = form.submit;
    form.addEventListener('submit', preventGet, true);
    Object.defineProperty(form, 'submit', {configurable: true, value: function (...args) {
      if (form.method.toUpperCase() === 'POST') return nativeSubmit.apply(form, args);
    }});
    Object.assign(state, {authTarget: target, authUntil: Date.now() + 10000, agentAuthGuard: {form, preventGet, submitDescriptor}});
  });
}

module.exports = {agentAuthenticate};

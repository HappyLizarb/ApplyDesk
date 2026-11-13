const crypto = require('node:crypto');

// The same browser element keeps its identifier until the document is replaced.
function collectPage() {
  const state = globalThis.__applyDesk;
  const visible = element => !!(element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const dialogs = [...document.querySelectorAll('[role="dialog"],dialog[open]')].filter(visible);
  const root = dialogs.length === 1 ? dialogs[0] : document;
  const label = element => clean(element.labels?.length ? [...element.labels].map(x => x.innerText).join(' ') :
    element.getAttribute('aria-label') || (element.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' '));
  const identity = element => {
    if (!state.ids.has(element)) state.ids.set(element, `${state.documentId}-${++state.counter}`);
    const id = state.ids.get(element); state.elements.set(id, element); return id;
  };
  const fields = [...root.querySelectorAll('input,select,textarea,[role="combobox"],[role="listbox"],[contenteditable="true"]')]
    .filter(element => (visible(element) || (element.type === 'file' && [...element.labels || []].some(visible))) && !['hidden', 'submit', 'button', 'reset', 'image'].includes(element.type))
    .map(element => describeField(element, identity, label, clean));
  const buttons = [...root.querySelectorAll('button,input[type="submit"],input[type="button"],[role="button"]')]
    .filter(visible).map(element => describeButton(element, identity, clean));
  const text = document.body?.innerText || '';
  return {url: location.href, title: document.title, text: text.slice(0, 60000), text_truncated: text.length > 60000, fields, buttons,
    links: [...document.querySelectorAll('a[href]')].filter(visible).slice(0, 300)
      .map(element => ({id: identity(element), text: clean(element.innerText), href: element.href, url: element.href})),
    warnings: [...([...document.querySelectorAll('iframe')].filter(visible).length ? ['Embedded frames require manual inspection.'] : []),
      ...([...document.querySelectorAll('*')].some(element => element.shadowRoot) ? ['Shadow DOM controls require manual inspection.'] : [])]};
}

// Labels, group legends, native options, and actual values are inspected together.
function describeField(element, identity, label, clean) {
  const role = element.getAttribute('role');
  const type = element.tagName === 'SELECT' ? 'select' : ['combobox', 'listbox'].includes(role) ? role : element.type || role || element.tagName.toLowerCase();
  const group = element.closest('fieldset,[role="group"],[role="radiogroup"]');
  const context = element.closest('form,section,main,[role="form"],[role="dialog"],dialog,[data-hook="right-content"]');
  const supported = ['text', 'email', 'tel', 'url', 'number', 'date', 'month', 'password', 'search', 'textarea', 'select', 'checkbox', 'radio', 'file'].includes(type);
  return {field_id: identity(element), type, label: label(element), name: element.name || '',
    placeholder: element.getAttribute('placeholder') || '', group: clean(group?.querySelector('legend')?.innerText || group?.getAttribute('aria-label')),
    required: !!element.required || element.getAttribute('aria-required') === 'true', disabled: !!element.disabled || element.matches(':disabled'), readonly: !!element.readOnly,
    value: type === 'password' ? null : type === 'file' ? null : element.value ?? element.textContent,
    checked: ['radio', 'checkbox'].includes(type) ? element.checked : undefined, supported,
    options: type === 'select' ? [...element.options].map(option => ({value: option.value, label: clean(option.text), disabled: option.disabled, selected: option.selected})) : undefined,
    files: type === 'file' ? [...element.files].map(file => ({name: file.name, size: file.size, type: file.type})) : undefined,
    multiple: !!element.multiple, accept: element.accept || '', autocomplete: element.autocomplete || '',
    form_id: element.form ? identity(element.form) : null, group_id: group ? identity(group) : null,
    context_id: context ? identity(context) : null,
    form_action: element.form?.action || '', form_method: element.form?.method || ''};
}

// A button's native form destination is needed for tightly scoped authentication.
function describeButton(element, identity, clean) {
  const form = element.form || element.closest('form');
  const context = element.closest('form,section,main,[role="form"],[role="dialog"],dialog,[data-hook="right-content"]');
  // Hidden inputs may select another employer; their meaning cannot be inferred safely.
  const authInputs = form ? [...form.elements].filter(input => ['INPUT', 'SELECT', 'TEXTAREA'].includes(input.tagName) && !['submit', 'button'].includes(input.type)) : [];
  const authNames = /^(user(name)?|email(address)?|password|confirm(password)?|first(name)?|last(name)?|full(name)?|name|remember(me)?|terms|privacy|agree|csrf)$/i;
  const authOnly = authInputs.every(input => input.getClientRects().length && getComputedStyle(input).visibility === 'visible' &&
    (['email', 'password'].includes(input.type) || (['text', 'checkbox'].includes(input.type) && authNames.test((input.name || '').replace(/[-_\s]/g, '')))));
  return {field_id: identity(element), text: clean(element.innerText || element.value || element.getAttribute('aria-label')),
    type: element.type || '', disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true',
    form_id: form ? identity(form) : null, context_id: context ? identity(context) : null, context_text: clean(context?.innerText).slice(0, 12000),
    form_action: form?.action || '', form_method: (form?.method || '').toUpperCase(),
    auth_form: !!(form?.querySelector('input[type="password"]') && authOnly)};
}

// Embed helpers in one evaluation so browser globals never expose executable tools.
const inspectionScript = `(() => { const describeField = ${describeField}; const describeButton = ${describeButton}; return (${collectPage})(); })()`;

// Hash values as well as field identities to reject stale prefills and changed options.
async function inspect(page, state) {
  const snapshot = await page.evaluate(inspectionScript);
  await hashAttachments(page, snapshot);
  if (page.context().pages().filter(candidate => !candidate.isClosed()).length > 1) snapshot.warnings.push('An additional browser tab opened; manual inspection is required.');
  const fingerprint = JSON.stringify({url: snapshot.url, text: snapshot.text, text_truncated: snapshot.text_truncated, fields: snapshot.fields, buttons: snapshot.buttons, warnings: snapshot.warnings});
  snapshot.snapshot_id = crypto.createHash('sha256').update(fingerprint).digest('hex');
  snapshot.guard = {manual: state.manual, mutations_blocked: !state.manual && !state.applicationSource, submission_guarded: !state.manual,
    blocked_requests: state.blocked.filter(item => item.reason !== 'Unverified page telemetry.'),
    blocked_telemetry_count: state.blocked.filter(item => item.reason === 'Unverified page telemetry.').length};
  if (state.applicationSource) snapshot.application_source = state.applicationSource;
  snapshot.buttons = snapshot.buttons.map(button => ({...button, action: classify(button.text)}));
  return snapshot;
}

// Hash actual browser File contents, independently from the local upload source.
async function hashAttachments(page, snapshot) {
  let totalBytes = 0;
  for (const field of snapshot.fields.filter(item => item.type === 'file')) {
    for (const [index, file] of field.files.entries()) {
      totalBytes += file.size;
      if (file.size > 20 * 1024 * 1024 || totalBytes > 40 * 1024 * 1024) { file.hash_error = 'Attachment exceeds independent audit size limit.'; continue; }
      const result = await page.evaluate(async ({id, index}) => {
        const file = globalThis.__applyDesk.elements.get(id)?.files[index];
        if (!file) return {error: 'Attachment changed during inspection.'};
        const bytes = new Uint8Array(await file.arrayBuffer());
        if (crypto.subtle) {
          const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
          return {sha256: [...digest].map(value => value.toString(16).padStart(2, '0')).join('')};
        }
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
        return {base64: btoa(binary)};
      }, {id: field.field_id, index});
      file.sha256 = result.sha256 || (result.base64 !== undefined ? crypto.createHash('sha256').update(Buffer.from(result.base64, 'base64')).digest('hex') : null);
      if (result.error) file.hash_error = result.error;
    }
  }
}

// An allowlist protects against vague actions such as “Done” or “Finish”.
function classify(text) {
  const value = text.toLowerCase().replace(/\s+/g, ' ').trim();
  if (/submit|send application|finish|complete application|apply now|confirm application/.test(value)) return 'final_or_ambiguous';
  if (/^(next|continue|save and continue|save & continue)( step)?[ ›»→]*$/.test(value)) return 'advance';
  if (/^(log in|login|sign in)$/.test(value)) return 'login';
  if (/^(create account|create an account|sign up|register)$/.test(value)) return 'create_account';
  return 'unknown';
}

module.exports = {inspect, classify};

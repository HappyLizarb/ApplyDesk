// Browser-side observations retain real elements while exposing bounded, inert descriptions.
function collectAgentPage() {
  const state = globalThis.__applyDesk;
  if (!state?.elements || !state.ids) throw new Error('The owned page is not initialized.');
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = element => getComputedStyle(element).visibility !== 'hidden' && !element.closest('[hidden],[inert]')
    && [...element.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
  const identity = element => {
    if (!element) return null;
    if (!state.ids.has(element)) state.ids.set(element, `${state.documentId}-${++state.counter}`);
    const id = state.ids.get(element); state.elements.set(id, element); return id;
  };
  const label = element => clean(element.getAttribute('aria-label') ||
    (element.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ') ||
    [...element.labels || []].map(agentLabelText).join(' ') || element.getAttribute('placeholder') ||
    (element.matches('button,a,[role="button"],[role="option"],summary') ? element.innerText : '') || element.title ||
    (['submit','button'].includes(element.type) ? element.value : ''));
  const candidates = [...document.querySelectorAll('input,select,textarea,button,a[href],summary,[role="button"],[role="combobox"],[role="listbox"],[role="option"],[role="checkbox"],[role="radio"],[contenteditable="true"]')];
  const eligible = candidates.filter(element => visible(element) || (element.type === 'file' && agentUploadControl(element, visible)))
    .filter(element => !['hidden', 'reset', 'image'].includes(element.type));
  const elements = eligible.slice(0, 300).map(element => describeAgentElement(element, {identity, label, clean, visible}));
  const secrets = candidates.filter(element => element.type === 'password').map(element => element.value).filter(Boolean);
  const redact = value => typeof value === 'string' ? secrets.reduce((text, secret) => text.split(secret).join('[redacted]'), value)
    : Array.isArray(value) ? value.map(redact) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redact(item)])) : value;
  const text = document.body?.innerText || '';
  return redact({url: location.href, title: document.title, text: text.slice(0, 24000),
    text_truncated: text.length > 24000, elements, elements_truncated: eligible.length > 300,
    dialogs: [...document.querySelectorAll('[role="dialog"],dialog[open]')].filter(visible)
      .map(element => ({id: identity(element), text: clean(element.innerText).slice(0, 3000)})),
    ...agentPageStatus({identity, label, clean, visible})});
}

// Informational alerts remain evidence; only actual invalid controls are validation errors.
function agentPageStatus({identity, label, clean, visible}) {
  const errors = [...document.querySelectorAll('input,select,textarea,[aria-invalid="true"]')].filter(visible)
    .filter(element => element.getAttribute('aria-invalid') === 'true' || element.willValidate && element.validity?.valid === false)
    .map(element => ({id: identity(element), invalid: true,
      text: clean(element.validationMessage || element.innerText || label(element)).slice(0, 500)}));
  const alerts = [...document.querySelectorAll('[role="alert"]')].filter(visible)
    .map(element => ({id: identity(element), text: clean(element.innerText).slice(0, 500)}));
  return {errors, alerts, loading: document.readyState !== 'complete'
    || [...document.querySelectorAll('[aria-busy="true"]')].some(visible)};
}

// Wrapping labels name a select without including every option or exposing its input value.
function agentLabelText(label) {
  const copy = label.cloneNode(true);
  for (const control of copy.querySelectorAll('input,select,textarea,button')) control.remove();
  return copy.textContent || '';
}

// A hidden native input is exposed only beside a visible upload trigger in a unique local group.
function agentUploadControl(input, visible) {
  const labels = [...input.labels || []].filter(visible);
  if (labels.length === 1) return labels[0];
  let group = input.parentElement;
  for (let depth = 0; group && depth < 3; depth++, group = group.parentElement) {
    if (group.matches('form,body,main') || group.querySelectorAll('input[type="file"]').length !== 1) break;
    const controls = [...group.querySelectorAll('button,[role="button"],label')].filter(visible);
    if (controls.length === 1) return controls[0];
  }
  return null;
}

// Native values, accessibility relationships and form membership stay separate from inferred meaning.
function describeAgentElement(element, {identity, label, clean, visible}) {
  const type = element.type || '', form = element.form || element.closest('form');
  const group = element.closest('fieldset,[role="group"],[role="radiogroup"]');
  const parent = group || element.parentElement;
  const uploadControl = type === 'file' ? agentUploadControl(element, visible) : null;
  const authText = form?.querySelector('input[type="password"]') && element.matches('button,[role="button"],input[type="submit"]') ? clean(form.innerText) : '';
  return {id: identity(element), tag: element.tagName.toLowerCase(), role: element.getAttribute('role') || '', type,
    label: label(element),
    context: clean(parent?.innerText).slice(0, 1000), name: element.name || '', visible: visible(element),
    required: !!element.required || element.getAttribute('aria-required') === 'true',
    disabled: !!element.disabled || element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true',
    readonly: !!element.readOnly && !['radio','checkbox','file'].includes(type),
    value: type === 'password' || type === 'file' ? null : element.tagName === 'SELECT' && element.multiple
      ? [...element.selectedOptions].map(option => option.value) : element.value ?? (element.isContentEditable ? element.innerText : null),
    checked: ['checkbox','radio'].includes(type) ? element.checked : element.getAttribute('aria-checked'),
    selected: element.getAttribute('aria-selected'), expanded: element.getAttribute('aria-expanded'),
    controls: (element.getAttribute('aria-controls') || '').split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)).filter(Boolean).map(identity),
    form_id: identity(form), form_method: form?.method || '', form_action: form?.action || '',
    authentication_form: !!form?.querySelector('input[type="password"]'), group_id: identity(group),
    ...(authText ? {form_context: authText.slice(0, 6000), form_context_truncated: authText.length > 6000} : {}),
    href: element.matches('a[href]') ? element.href : null, download: element.hasAttribute('download'),
    options: element.tagName === 'SELECT' ? [...element.options].map(option => ({value: option.value, label: clean(option.text), disabled: option.disabled, selected: option.selected})) : [],
    files: type === 'file' ? [...element.files].map(file => ({name: file.name, size: file.size, type: file.type})) : [],
    accept: element.accept || '', multiple: !!element.multiple, upload_control_id: identity(uploadControl)};
}

module.exports = {collectAgentPage, describeAgentElement, agentUploadControl, agentLabelText, agentPageStatus};

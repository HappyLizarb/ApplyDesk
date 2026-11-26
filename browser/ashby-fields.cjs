// Bind Ashby's form-less controls to one visible posting panel with its own heading.
function ashbyContext(element) {
  if (location.hostname !== 'jobs.ashbyhq.com') return null;
  const pane = element.closest('.ashby-job-posting-right-pane');
  if (!pane || pane.querySelectorAll('.ashby-application-form-submit-button').length !== 1) return null;
  for (let root = pane; root && root !== document.body; root = root.parentElement) {
    if (root.querySelectorAll('.ashby-job-posting-right-pane').length > 1) return null;
    const headings = [...root.querySelectorAll('h1')].filter(item=>item.getClientRects().length);
    if (headings.length === 1) return root;
  }
  return null;
}

// Ashby places question titles directly under its field container rather than in legends.
function ashbyField(element, identity, clean) {
  if (location.hostname !== 'jobs.ashbyhq.com') return {};
  const group = element.closest('fieldset');
  const container = group || element.closest('.ashby-application-form-field-entry');
  const title = container?.querySelector(':scope > .ashby-application-form-question-title');
  if (!title) return {};
  const required = [...title.classList].some(name => name.startsWith('_required_')) || !!element.required;
  const path = container.getAttribute('data-field-path') || title.getAttribute('for');
  const checkboxEntry = element.type === 'checkbox' && group?.classList.contains('ashby-application-form-input-checkbox-group')
    ? element.id.replace(/-labeled-checkbox-\d+$/, '') : '';
  const entry = element.closest('[data-field-entry-id]')?.getAttribute('data-field-entry-id') || checkboxEntry || element.name;
  const form = entry?.endsWith('_' + path) ? entry.slice(0,-path.length-1) : '';
  return {required, ...(element.getAttribute('role')==='combobox'?{label:clean(title.innerText)}:{}), ashby_binding:{form,path}, ...(group ? {group: clean(title.innerText), group_id: identity(group)} : {})};
}

// Yes/no buttons expose one explicit selected state; the hidden checkbox is not the question.
function ashbyBooleans(root, identity, clean) {
  if (location.hostname !== 'jobs.ashbyhq.com') return [];
  return [...root.querySelectorAll('.ashby-application-form-input-yesno')].filter(el => el.getClientRects().length).map(element => {
    const container = element.closest('.ashby-application-form-field-entry');
    const title = container?.querySelector(':scope > .ashby-application-form-question-title');
    const buttons = [...element.querySelectorAll('button[data-option]')];
    const selected = buttons.filter(button => button.getAttribute('aria-pressed') === 'true');
    const form = element.closest('form'), context = form || ashbyContext(element) || element.closest('section,main,[role="form"],[role="dialog"]');
    const valid = buttons.length === 2 && new Set(buttons.map(b => b.dataset.option)).size === 2
      && buttons.every(b => ['yes','no'].includes(b.dataset.option)) && selected.length <= 1;
    return {...ashbyField(element, identity, clean), field_id: identity(element), type: 'select', widget: 'ashby_boolean', label: clean(title?.innerText),
      description: clean(container?.querySelector('.ashby-application-form-question-description')?.innerText),
      required: !!title && [...title.classList].some(name => name.startsWith('_required_')), supported: valid,
      value: selected[0]?.dataset.option || '', disabled: buttons.every(b => b.disabled), readonly: false, multiple: false,
      options: buttons.map(b => ({value:b.dataset.option,label:clean(b.innerText),disabled:b.disabled,selected:b.getAttribute('aria-pressed')==='true'})),
      form_id: form ? identity(form) : null, context_id: context ? identity(context) : null};
  });
}

// Use the exact inspected widget and require an independent selected-state readback.
async function selectAshbyBoolean(element, value, check) {
  if (!['yes','no'].includes(value)) throw new Error('The yes/no answer must match an observed option.');
  const buttons = await element.$$(`button[data-option="${value}"]`);
  if (buttons.length !== 1) throw new Error('The yes/no question changed. Inspect it again.');
  check(); await buttons[0].click({signal: check.signal});
  const selected = await element.evaluate(root => [...root.querySelectorAll('button[aria-pressed="true"]')].map(item => item.dataset.option));
  if (selected.length !== 1 || selected[0] !== value) throw new Error('The yes/no answer could not be verified.');
  return value;
}

module.exports = {ashbyContext, ashbyField, ashbyBooleans, selectAshbyBoolean};

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {inspect} = require('./inspect.cjs');

// A caller must supply the exact inspected version before any browser mutation.
async function fresh(page, state, snapshotId) {
  if (state.manual) throw new Error('Browser is under manual control. Reopen the application to resume automation.');
  const snapshot = await inspect(page, state);
  if (!snapshotId || snapshot.snapshot_id !== snapshotId) throw new Error('The form changed. Inspect it again before filling.');
  return snapshot;
}

// Use the retained DOM element, never a broad selector or a guessed list position.
async function elementFor(page, id, allowHiddenFile = false) {
  const handle = await page.evaluateHandle(id => globalThis.__applyDesk?.elements.get(id), id);
  const element = handle.asElement();
  if (!element || (!await element.isVisible() && !(allowHiddenFile && await element.evaluate(input => input.type === 'file')))) throw new Error('Field is missing or no longer visible.');
  return element;
}

// The mapper resolves visible meaning; the controller accepts only an exact opaque value.
function optionValue(field, value) {
  const matches = field.options.filter(option => !option.disabled && option.value === String(value));
  if (matches.length !== 1) throw new Error('Select answer does not identify one exact enabled option.');
  return matches[0].value;
}

// Only supported controls can be changed, and existing values are never silently trusted.
async function setAnswer(page, field, answer) {
  if (!field || !field.supported || field.disabled || field.type === 'file') throw new Error('Unsupported, missing, disabled, or file field.');
  const element = await elementFor(page, field.field_id);
  let expected = answer.value;
  if (field.readonly) {
    if (await element.inputValue() !== expected) throw new Error('Readonly field does not match the confirmed answer.');
  } else if (['checkbox', 'radio'].includes(field.type)) {
    if (typeof expected !== 'boolean' || (field.type === 'radio' && !expected)) throw new Error('Checkbox/radio answers must be explicit booleans; radio can only be selected.');
    await element.setChecked(expected); expected = !!expected;
  } else if (field.type === 'select') {
    if (field.multiple) throw new Error('Multiple-choice select widgets require manual control.');
    expected = optionValue(field, expected); await element.selectOption({value: expected});
  } else {
    if (typeof expected !== 'string') throw new Error('Text field answers must be strings.');
    await element.fill(expected);
  }
  const actual = ['checkbox', 'radio'].includes(field.type) ? await element.isChecked() : await element.inputValue();
  if (actual !== expected) throw new Error('Field readback did not match the intended value.');
  return field.type === 'password' ? {field_id: field.field_id, matches: true} : {field_id: field.field_id, matches: true, value: actual};
}

// File identity is verified before upload; only declared document folders are accessible.
async function setUpload(page, field, upload, roots) {
  if (!field || field.type !== 'file' || field.disabled) throw new Error('The selected control is not an enabled file input.');
  const resolved = await fs.realpath(upload.path);
  const allowed = await Promise.all(roots.map(root => fs.realpath(root)));
  if (!allowed.some(root => resolved.startsWith(root + path.sep))) throw new Error('Upload is outside the approved document folder.');
  const data = await fs.readFile(resolved);
  const hash = crypto.createHash('sha256').update(data).digest('hex');
  if (!upload.sha256 || upload.sha256 !== hash) throw new Error('Document changed or attachment hash is missing.');
  const element = await elementFor(page, field.field_id, true);
  await element.setInputFiles(resolved);
  const files = await element.evaluate(input => [...input.files].map(file => ({name: file.name, size: file.size})));
  if (files.length !== 1 || files[0].name !== path.basename(resolved) || files[0].size !== data.length) throw new Error('Attachment readback failed.');
  return {field_id: field.field_id, matches: true, files, sha256: hash, upload_confirmed: false};
}

// Value changes in other controls may be resume autofill or conditional form changes.
function unexpectedChange(before, after, fieldId) {
  const target = before.fields.find(field => field.field_id === fieldId);
  const stable = snapshot => snapshot.fields.map(field => {
    const copy = {...field};
    if (field.field_id === fieldId) {
      delete copy.value; delete copy.checked; delete copy.files;
      if (copy.options) copy.options = copy.options.map(({selected, ...option}) => option);
    }
    if (target?.type === 'radio' && target.name && field.type === 'radio' && field.name === target.name && field.form_id === target.form_id) delete copy.checked;
    return copy;
  });
  return JSON.stringify(stable(before)) !== JSON.stringify(stable(after));
}

// Stop a batch after dynamic fields or autofill appear so the caller can audit anew.
async function fill(page, state, params) {
  let before = await fresh(page, state, params.snapshot_id);
  const readbacks = [];
  const operations = [...(params.answers || []).map(item => ({...item, kind: 'answer'})), ...(params.uploads || []).map(item => ({...item, kind: 'upload'}))];
  if (new Set(operations.map(item => item.field_id)).size !== operations.length) throw new Error('A field may only appear once per batch.');
  for (const operation of operations) {
    const field = before.fields.find(item => item.field_id === operation.field_id);
    readbacks.push(operation.kind === 'upload' ? await setUpload(page, field, operation, state.uploadRoots) : await setAnswer(page, field, operation));
    await page.waitForTimeout(120);
    const after = await inspect(page, state);
    if (unexpectedChange(before, after, operation.field_id)) return {...after, readbacks, intervention: 'The form changed or populated other fields. Inspect and audit the new state.'};
    before = after;
  }
  return {...before, readbacks};
}

// Native form posts are deliberately blocked even when their button says “Next”.
async function advance(page, state, params) {
  const snapshot = await fresh(page, state, params.snapshot_id);
  if (snapshot.buttons.some(button => button.action === 'final_or_ambiguous')) throw new Error('A possible final submission boundary is visible. Automatic navigation has stopped.');
  const button = snapshot.buttons.find(item => item.field_id === params.field_id);
  if (!button || button.disabled || button.action !== 'advance') throw new Error('Only a verified Next or Continue navigation button is allowed.');
  if (button.type === 'submit' && button.form_action) throw new Error('Native form submission requires manual control, even when labeled Next.');
  const element = await elementFor(page, button.field_id);
  await element.click();
  await page.waitForTimeout(250);
  return inspect(page, state);
}

// Authentication permits one exact, separate native form destination for ten seconds.
async function authenticate(page, state, params) {
  const snapshot = await fresh(page, state, params.snapshot_id);
  const button = snapshot.buttons.find(item => item.field_id === params.field_id);
  const intent = params.intent === 'sign_in' ? 'login' : params.intent;
  if (!['login', 'create_account'].includes(intent) || button?.action !== intent || !button.auth_form) throw new Error('A distinct, verified authentication form is required.');
  const url = new URL(button.form_action || snapshot.url);
  if (url.origin !== new URL(snapshot.url).origin || !/\/(login|sign-?in|auth|register|sign-?up)(\/|$)/i.test(url.pathname) || button.form_method !== 'POST') throw new Error('Authentication destination is not a separate same-origin POST endpoint. Use manual control.');
  state.auth = {url: url.href, method: 'POST', expires: Date.now() + 10000};
  await page.evaluate(expires => { globalThis.__applyDesk.authUntil = expires; }, state.auth.expires);
  try { await (await elementFor(page, button.field_id)).click(); await page.waitForTimeout(400); }
  finally { state.auth = null; await page.evaluate(() => { if (globalThis.__applyDesk) globalThis.__applyDesk.authUntil = 0; }).catch(() => {}); }
  return inspect(page, state);
}

module.exports = {fill, advance, authenticate, fresh};

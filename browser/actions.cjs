const {fillWorkdayEducation}=require('./workday-education-fill.cjs');
const {uploadWorkdayResume}=require('./workday-upload.cjs');
const {selectAshbyOption}=require('./ashby-select.cjs');
const {fillAshbyEducation}=require('./ashby-education-fill.cjs');
const {armWorkdayDraft}=require('./workday-drafts.cjs');
const {setWorkdayControl}=require('./workday-controls.cjs');
const {successFactorsAuthPermit} = require('./successfactors-auth.cjs');
const {settleResearchPage,workdayFormState} = require('./page-settling.cjs');
const {uploadAshbyResume} = require('./ashby-upload.cjs');
const {armAshbyAnswer, waitForAshbySave} = require('./ashby-drafts.cjs');
const fs = require('node:fs/promises');
const {workdayAuthPermit, workdayAuthClickTarget} = require('./workday-auth.cjs');
const {selectAshbyBoolean} = require('./ashby-fields.cjs');
const path = require('node:path');
const crypto = require('node:crypto');
const {inspect} = require('./inspect.cjs');
const {checkpoint} = require('./control.cjs');
const {selectGreenhouse} = require('./greenhouse.cjs');
const {uploadResume} = require('./greenhouse-upload.cjs');

// A caller must supply the exact inspected version before any browser mutation.
async function fresh(page, state, snapshotId) {
  const check = checkpoint(state);check();
  if (state.manual) throw new Error('Browser is under manual control. Reopen the application to resume automation.');
  const snapshot = await inspect(page, state);
  check();
  if (!snapshotId || snapshot.snapshot_id !== snapshotId) throw new Error('The form changed. Inspect it again before filling.');
  return snapshot;
}

// Use the retained DOM element, never a broad selector or a guessed list position.
async function elementFor(page, id, allowHiddenFile = false) {
  const handle = await page.evaluateHandle(id => globalThis.__applyDesk?.elements.get(id), id);
  const element = handle.asElement();
  if (!element || (!await element.isVisible() && !(allowHiddenFile && await element.evaluate(input => input.type === 'file')))) {
    const error = new Error('Field is missing or no longer visible.');
    error.code = 'STALE_FIELD';throw error;
  }
  return element;
}

// The mapper resolves visible meaning; the controller accepts only an exact opaque value.
function optionValue(field, value) {
  const matches = field.options.filter(option => !option.disabled && option.value === String(value));
  if (matches.length !== 1) throw new Error('Select answer does not identify one exact enabled option.');
  return matches[0].value;
}

// Only supported controls can be changed, and existing values are never silently trusted.
async function setAnswer(page, field, answer, check, state) {
  if (!field || !field.supported || field.disabled || field.type === 'file') throw new Error('Unsupported, missing, disabled, or file field.');
  const element = await elementFor(page, field.field_id);
  let expected = answer.value;
  if(['workday_education','ashby_education'].includes(field.widget)) {
    const fillEducation=field.widget==='workday_education'?fillWorkdayEducation:fillAshbyEducation;
    const value=await fillEducation(page,state,field,expected,check);
    return {field_id:field.field_id,matches:true,value};
  }
  const edit = armAshbyAnswer(state, field, expected);
  if(field.widget==='ashby_select') {
    const value=await selectAshbyOption(page,element,field,expected,edit,check);
    return {field_id:field.field_id,matches:true,value};
  }
  if (['workday_select','workday_phone_code'].includes(field.widget)) {
    const value=await setWorkdayControl(page,element,field,expected,check);
    return {field_id:field.field_id,matches:true,value};
  }
  if (field.widget === 'ashby_boolean') {
    const value = await selectAshbyBoolean(element, expected, check);
    await waitForAshbySave(edit, check);
    return {field_id: field.field_id, matches: true, value};
  }
  if (field.widget === 'greenhouse_select') {
    const value = await selectGreenhouse(page, element, expected, check);
    return {field_id: field.field_id, matches: true, value};
  }
  return setNativeAnswer(element,field,expected,check,edit);
}

// Native HTML controls use their own checked/value readback after any employer autosave.
async function setNativeAnswer(element,field,expected,check,edit) {
  if (field.readonly) {
    if (await element.inputValue() !== expected) throw new Error('Readonly field does not match the confirmed answer.');
  } else if (['checkbox', 'radio'].includes(field.type)) {
    if (typeof expected !== 'boolean' || (field.type === 'radio' && !expected)) throw new Error('Checkbox/radio answers must be explicit booleans; radio can only be selected.');
    check();await element.setChecked(expected, {signal: check.signal}); expected = !!expected;
  } else if (field.type === 'select') {
    if (field.multiple) throw new Error('Multiple-choice select widgets require manual control.');
    expected = optionValue(field, expected); check();await element.selectOption({value: expected}, {signal: check.signal});
  } else {
    if (typeof expected !== 'string') throw new Error('Text field answers must be strings.');
    check();await element.fill(expected, {signal: check.signal});
  }
  await waitForAshbySave(edit, check);
  const actual = ['checkbox', 'radio'].includes(field.type) ? await element.isChecked() : await element.inputValue();
  if (actual !== expected) throw new Error('Field readback did not match the intended value.');
  return field.type === 'password' ? {field_id: field.field_id, matches: true} : {field_id: field.field_id, matches: true, value: actual};
}

// File identity is verified before upload; only declared document folders are accessible.
async function setUpload(page, field, upload, roots, check, state) {
  if (!field || field.type !== 'file' || field.disabled) throw new Error('The selected control is not an enabled file input.');
  const resolved = await fs.realpath(upload.path);
  const allowed = await Promise.all(roots.map(root => fs.realpath(root)));
  if (!allowed.some(root => resolved.startsWith(root + path.sep))) throw new Error('Upload is outside the approved document folder.');
  const data = await fs.readFile(resolved);
  const hash = crypto.createHash('sha256').update(data).digest('hex');
  if (!upload.sha256 || upload.sha256 !== hash) throw new Error('Document changed or attachment hash is missing.');
  if(field.widget==='workday_resume') {
    const group=await elementFor(page,field.field_id);
    const input=field.remote_uploaded?null:await elementFor(page,field.upload_input_id,true);
    return uploadWorkdayResume(page,state,field,input,group,resolved,hash,data,check);
  }
  const element = field.remote_uploaded ? null : await elementFor(page, field.field_id, true);
  if (field.widget === 'greenhouse_upload') return uploadResume(page, state, field, element, resolved, hash, data, check);
  if (field.ashby_binding?.path === '_systemfield_resume') await uploadAshbyResume(page, state, field, element, resolved, hash, data, check);
  else { check();await element.setInputFiles(resolved, {signal: check.signal}); }
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

// Bring the exact changed field into view without simulated keystrokes or guessed selectors.
async function presentField(page, field, state, check) {
  check();
  if (!state.pace || !field) return;
  const element = await elementFor(page, field.field_id, field.type === 'file');
  if (await element.isVisible()) await element.scrollIntoViewIfNeeded({signal: check.signal});
  await page.waitForTimeout(200);
  check();
}

// Stop a batch after dynamic fields or autofill appear so the caller can audit anew.
async function fill(page, state, params) {
  const check = checkpoint(state);
  let before = await fresh(page, state, params.snapshot_id);
  const readbacks = [];
  const operations = [...(params.answers || []).map(item => ({...item, kind: 'answer'})), ...(params.uploads || []).map(item => ({...item, kind: 'upload'}))];
  if (new Set(operations.map(item => item.field_id)).size !== operations.length) throw new Error('A field may only appear once per batch.');
  for (const operation of operations) {
    const field = before.fields.find(item => item.field_id === operation.field_id);
    try {
      await presentField(page, field, state, check);
      readbacks.push(operation.kind === 'upload' ? await setUpload(page, field, operation, state.uploadRoots, check, state) : await setAnswer(page, field, operation, check, state));
    } catch (error) {
      // A reactive page may replace a clicked control; replan without replaying the batch.
      if (operation.kind !== 'answer' || (error.code !== 'STALE_FIELD' && !/Element is not attached to the DOM/.test(error.message)))
        throw new Error(`Could not fill ${String(field?.label||field?.name||'the selected field').slice(0,150)}: ${error.message}`);
      check();return {...await inspect(page,state),readbacks,intervention:'field_replaced_during_fill'};
    }
    await page.waitForTimeout(state.pace ? 300 : 120);
    check();
    const after = await inspect(page, state);
    if (unexpectedChange(before, after, operation.field_id)) return {...after, readbacks, intervention: 'The form changed or populated other fields. Inspect and audit the new state.'};
    before = after;
  }
  return {...before, readbacks};
}

// Audited native steps work in the user's Chrome while final controls remain separate.
async function advance(page, state, params) {
  const check = checkpoint(state);
  const snapshot = await fresh(page, state, params.snapshot_id);
  const button = snapshot.buttons.find(item => item.field_id === params.field_id);
  if (button?.navigation_kind !== 'account_entry' && snapshot.buttons.some(button => button.action === 'final_or_ambiguous')) throw new Error('A possible final submission boundary is visible. Automatic navigation has stopped.');
  if (!button || button.disabled || button.action !== 'advance') throw new Error('Only a verified Next or Continue navigation button is allowed.');
  const accountNavigation=['account_entry','account_privacy'].includes(button.navigation_kind);
  const hasAnswers=snapshot.fields.some(field=>field.required || (['radio','checkbox'].includes(field.type)?field.checked:field.type==='file'?field.files?.length:field.value));
  if (state.normalNetworking && hasAnswers && !accountNavigation && (!params.audit?.passed || params.audit.snapshot_id!==snapshot.snapshot_id)) throw new Error('Review the current fields before continuing.');
  if (!state.normalNetworking && button.type === 'submit' && button.form_action) throw new Error('Native form submission requires manual control, even when labeled Next.');
  const workday=!state.normalNetworking && await armWorkdayDraft(page,state,snapshot,button,params.audit);
  const element = await elementFor(page, button.field_id);
  const previousWorkdayStep=state.normalNetworking?await page.evaluate(workdayFormState):null;
  check();await element.click({signal: check.signal});
  if (state.normalNetworking || workday || ['account_entry','account_privacy'].includes(button.navigation_kind)) await settleResearchPage(page,{previousWorkdayStep,check});
  else await page.waitForTimeout(250);
  check();
  return inspect(page, state);
}

// Authentication permits one exact, separate native form destination for ten seconds.
async function authenticate(page, state, params) {
  const check = checkpoint(state);
  const snapshot = await fresh(page, state, params.snapshot_id);
  const button = snapshot.buttons.find(item => item.field_id === params.field_id);
  const intent = params.intent === 'sign_in' ? 'login' : params.intent;
  if (!['login', 'create_account'].includes(intent) || button?.action !== intent || !button.auth_form) throw new Error('A distinct, verified authentication form is required.');
  const url = new URL(button.form_action || snapshot.url);
  if (url.origin !== new URL(snapshot.url).origin || (button.auth_widget !== 'successfactors' && !/\/(login|sign-?in|auth|register|sign-?up)(\/|$)/i.test(url.pathname)) || button.form_method !== 'POST') throw new Error('Authentication destination is not a separate same-origin POST endpoint. Use manual control.');
  check();const expires = Date.now() + 10000;
  state.auth = button.auth_widget === 'workday' ? await workdayAuthPermit(page, snapshot, button, expires) : {url: url.href, method: 'POST', expires};
  if (button.auth_widget === 'successfactors') state.auth = await successFactorsAuthPermit(page,snapshot,button,expires);
  await page.evaluate(expires => { globalThis.__applyDesk.authUntil = expires; }, state.auth.expires);
  const response = ['workday','successfactors'].includes(button.auth_widget)
    ? page.waitForResponse(item => item.url() === url.href && item.request().method() === 'POST', {timeout: 15000}).catch(() => null) : null;
  try {
    let element = await elementFor(page, button.field_id);
    if (button.auth_widget === 'workday') element = await workdayAuthClickTarget(element, button);
    await element.evaluate((target, expires) => { globalThis.__applyDesk.authTarget = target; globalThis.__applyDesk.authUntil = expires; }, expires);
    check();await element.click({signal: check.signal});
    if (response && !await response) throw new Error('Account request outcome is unconfirmed. Check the existing account before retrying.');
    await settleResearchPage(page);check();
  }
  finally { state.auth = null; await page.evaluate(() => { if (globalThis.__applyDesk) { globalThis.__applyDesk.authUntil = 0; globalThis.__applyDesk.authTarget = null; } }).catch(() => {}); }
  return inspect(page, state);
}

module.exports = {fill, advance, authenticate, fresh};

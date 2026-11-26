const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const {installOwnedPageGuards} = require('../browser/guard.cjs');
const {agentObserve} = require('../browser/agent-page.cjs');
const {agentAction} = require('../browser/agent-page-actions.cjs');
const {agentAuthenticate} = require('../browser/agent-page-auth.cjs');
const {revoke} = require('../browser/control.cjs');

const html = `<!doctype html><title>Unseen employer</title><main><h1>Engineer application</h1>
<div role="dialog"><p>Optional analytics cookies</p><button type="button" onclick="this.parentElement.remove()">Reject optional cookies</button></div>
<form id="application" onsubmit="event.preventDefault();document.querySelector('#step').textContent='Step 2'">
<span id="step">Step 1</span><label>First name<input name="first"></label>
<label>Country<input role="combobox" aria-controls="countries" readonly onclick="document.querySelector('#countries').hidden=false"></label>
<div id="countries" role="listbox" hidden><div role="option" onclick="document.querySelector('[role=combobox]').value=this.textContent;this.parentElement.hidden=true">United States</div></div>
<label>Seniority<select><option value="new">New graduate</option><option value="senior">Senior</option></select></label>
<label>Locations<select multiple><option value="east">East</option><option value="west">West</option></select></label>
<fieldset><legend>Work authorized?</legend><label><input type="radio" name="authorized" readonly>Yes</label></fieldset>
<div><input type="file" hidden><button type="button">Select file — Upload resume</button></div>
<div><input type="file" hidden><button type="button">Select file — Cover letter</button></div>
<input type="file" hidden id="unassociated"><button type="submit">Next</button>
<button type="button" onclick="window.sent=true">Submit application</button></form>
<form><label>Password<input type="password" value="secret-that-must-not-leak"></label><button type="submit">Sign in</button></form>
<button type="button" id="busy" style="display:none">Not available</button></main>`;

// Every mutation uses the latest observation rather than reusing a selector or stale node ID.
async function act(page, state, label, action, value, extra = {}) {
  const observed = await agentObserve(page, state);
  const element = observed.elements.find(item => item.label === label);
  assert(element, `Missing observed control: ${label}`);
  return agentAction(page, state, {snapshot_id: observed.snapshot_id, element_id: element.id,
    action, value, intent: 'explore', ...extra});
}

// A new employer's native and ARIA controls can be explored without a provider adapter.
async function testExploration(page, state) {
  const initial = await agentObserve(page, state);
  assert.equal(initial.dialogs.length, 1);
  assert(!JSON.stringify(initial).includes('secret-that-must-not-leak'));
  assert.equal(initial.elements.find(item => item.type === 'password').value, null);
  assert.equal(initial.elements.filter(item => item.type === 'file').length, 2);
  await act(page, state, 'Reject optional cookies', 'click');
  assert.equal((await agentObserve(page, state)).dialogs.length, 0);
  await act(page, state, 'First name', 'fill', 'Example');
  await act(page, state, 'Country', 'click');
  const open = await agentObserve(page, state);
  assert(open.elements.some(item => item.role === 'option' && item.label === 'United States'));
  await act(page, state, 'United States', 'click');
  assert.equal((await agentObserve(page, state)).elements.find(item => item.role === 'combobox').value, 'United States');
  await act(page, state, 'Seniority', 'select', 'new');
  const multiple = await act(page, state, 'Locations', 'select', ['east','west']);
  assert.deepEqual(multiple.elements.find(item => item.label === 'Locations').value, ['east','west']);
  await act(page, state, 'Yes', 'check', true);
  assert.equal(await page.locator('input[type=radio]').isChecked(), true);
  await assert.rejects(act(page, state, 'Password', 'fill', 'bad'), /protected account/);
}

// A form edit invalidates the action; changed nodes and stopping never silently replay it.
async function testFreshness(page, state) {
  const before = await agentObserve(page, state), first = before.elements.find(item => item.label === 'First name');
  await page.locator('input[name=first]').evaluate(input => {input.outerHTML='<input name="first" value="Changed">';});
  const result = await agentAction(page, state, {snapshot_id: before.snapshot_id, element_id: first.id,
    action: 'fill', value: 'Not written', intent: 'explore'});
  assert.equal(result.action_status, 'stale');
  assert.equal(await page.locator('input[name=first]').inputValue(), 'Changed');
  const now = await agentObserve(page, state);
  await assert.rejects(agentAction(page, state, {snapshot_id: now.snapshot_id, element_id: first.id,
    action: 'fill', value: 'Not written', intent: 'explore'}), /current observation/);
  revoke(state);
  await assert.rejects(agentObserve(page, state), /paused/);
  state.paused = false;state.controller = new AbortController();
}

// A progression click is explicit, while an application submission cannot use generic click.
async function testBoundaries(page, state) {
  await assert.rejects(act(page, state, 'Submit application', 'click'), /separate reviewed submission/);
  assert.equal(await page.evaluate(() => window.sent), undefined);
  await assert.rejects(act(page, state, 'Next', 'click'), /explicit reviewed advance/);
  await act(page, state, 'Next', 'click', undefined, {intent: 'advance'});
  assert.equal(await page.locator('#step').innerText(), 'Step 2');
  const observed = await agentObserve(page, state);
  const waited = await agentAction(page, state, {snapshot_id: observed.snapshot_id, action: 'wait', value: 1, intent: 'explore'});
  assert.equal(waited.action_status, 'performed');
  await assert.rejects(agentObserve(page, {...state, workspace: false}), /owned application/);
}

// Exact approved document bytes are independently read back; unrelated hidden inputs stay unavailable.
async function testUpload(page, state, root) {
  const filename = path.join(root, 'resume.pdf'), bytes = Buffer.from('%PDF-1.4 synthetic verified resume');
  await fs.writeFile(filename, bytes);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const observed = await agentObserve(page, state), field = observed.elements.find(item => item.type === 'file' && item.context.includes('Upload resume'));
  assert(field); assert(field.upload_control_id);
  const result = await agentAction(page, state, {snapshot_id: observed.snapshot_id, element_id: field.id, action: 'upload',
    intent: 'explore', upload: {path: filename, sha256: digest, document_id: 'resume-1', kind: 'resume'}});
  assert.equal(result.result.upload.sha256, digest);
  assert.equal(result.result.upload.files[0].sha256, digest);
  assert.equal(result.result.upload.document_id, 'resume-1');
  assert.equal(result.result.upload.upload_confirmed, false);
  assert.equal(result.elements.find(item => item.id === field.id).files[0].sha256, digest);
  assert(!JSON.stringify(result).includes(root));
  const fresh = await agentObserve(page, state);
  await assert.rejects(agentAction(page, state, {snapshot_id: fresh.snapshot_id, element_id: field.id, action: 'upload',
    intent: 'explore', upload: {path: filename, sha256: '0'.repeat(64)}}), /document changed/);
}

// The private account path never exposes secrets or allows a native GET form to put them in a URL.
async function testAuthentication(page, state) {
  await page.setContent(`<form onsubmit="document.querySelector('#result').textContent=this.password.value;this.remove()">
    <label>Email<input name="email" type="email"></label><label>Password<input name="password" type="password"></label>
    <button type="button" onclick="window.menuOpened=true">More options</button><button>Sign in</button></form><p id="result"></p>`);
  const initial = await agentObserve(page, state), find = label => initial.elements.find(item => item.label === label);
  assert(['Email','Password','Sign in'].every(text => find('Sign in').form_context.includes(text)));
  await assert.rejects(act(page, state, 'Sign in', 'click'), /protected account/);
  await act(page, state, 'More options', 'click');
  assert.equal(await page.evaluate(() => window.menuOpened), true);
  const secret = 'dummy-"password\\with-escapes';
  const result = await agentAuthenticate(page, state, {snapshot_id: initial.snapshot_id,
    username_id: find('Email').id, password_ids: [find('Password').id], submit_id: find('Sign in').id,
    username: 'fixture@example.test', password: secret, intent: 'sign_in'});
  assert.equal(result.authentication_attempted, true);
  assert.equal(page.url(), 'about:blank');
  assert(!JSON.stringify(result).includes('dummy-'));
  assert((await agentObserve(page, state)).text.includes('[redacted]'));
}

// Hidden implementation controls and informational success alerts do not manufacture blockers.
async function testObservationFlags(page, state) {
  await page.setContent(`${'<input type="hidden">'.repeat(350)}<label>Required answer<input required></label>
    <div role="alert">Upload complete</div><div aria-busy="true" hidden>Old spinner</div>`);
  const initial = await agentObserve(page, state);
  assert.equal(initial.elements_truncated, false);
  assert.equal(initial.loading, false);
  assert.equal(initial.errors.length, 1);
  assert.equal(initial.errors[0].invalid, true);
  assert.equal(initial.alerts[0].text, 'Upload complete');
  await act(page, state, 'Required answer', 'fill', 'Confirmed');
  assert.deepEqual((await agentObserve(page, state)).errors, []);
  await page.locator('[aria-busy]').evaluate(element => {element.hidden = false;});
  assert.equal((await agentObserve(page, state)).loading, true);
  await page.setContent('<button type="button">Choice</button>'.repeat(301));
  assert.equal((await agentObserve(page, state)).elements_truncated, true);
}

// Tests use only an isolated synthetic browser page and temporary dummy document.
async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-agent-page-'));
  const browser = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page = await browser.newPage();
  const state = {workspace: true, normalNetworking: true, manual: false, paused: false, generation: 0,
    blocked: [], allowLocal: true, uploadRoots: [root], controller: new AbortController()};
  try {
    await installOwnedPageGuards(page, state);
    await page.setContent(html);
    await testExploration(page, state);
    await testBoundaries(page, state);
    await testUpload(page, state, root);
    await testFreshness(page, state);
    await testAuthentication(page, state);
    await testObservationFlags(page, state);
    console.log('Generic observed controls, custom options, cookies, fresh IDs, password privacy, uploads, Stop and final boundary passed.');
  } finally { await browser.close(); await fs.rm(root, {recursive: true, force: true}); }
}
main().catch(error => {console.error(error);process.exitCode = 1;});

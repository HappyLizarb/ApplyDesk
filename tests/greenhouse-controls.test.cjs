const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// No employer requests occur: a local fixture supplies a React-style accessible dropdown.
const html = `<title>Example application</title><form><label for="answer">Work permission</label>
<div class="select-shell"><div class="select__single-value"></div>
<input id="answer" role="combobox" aria-controls="choices" aria-required="true">
<input aria-hidden="true" tabindex="-1" required><div id="choices" role="listbox" hidden>
<div role="option">Yes</div><div role="option">No</div></div></div>
<div class="file-upload"><div class="label">Resume/CV*</div><label for="resume">Attach</label><input id="resume" type="file"></div>
</form><script>
const input=document.querySelector('#answer'), list=document.querySelector('#choices');
input.onclick=()=>{list.hidden=false};
input.oninput=()=>{for(const option of list.children)option.hidden=!option.textContent.toLowerCase().includes(input.value.toLowerCase())};
for(const option of list.children)option.onclick=()=>{document.querySelector('.select__single-value').textContent=option.textContent;input.value='';list.hidden=true};
</script>`;

// A declared POST endpoint must retain the embedded tenant and exact requisition.
async function destinationChecks(session) {
  await dispatch(session, 'open', {url: 'https://job-boards.greenhouse.io/embed/job_app?for=example&token=456'});
  await session.page.evaluate(() => {
    window.__remixContext = {state: {loaderData: {'routes/embed.job_app': {submitPath: 'https://boards.greenhouse.io/embed/example/jobs/456'}}}};
  });
  const before = await dispatch(session, 'snapshot', {});
  assert.equal(before.submission_target, 'https://boards.greenhouse.io/embed/example/jobs/456');
  for (const target of ['https://boards.greenhouse.io/embed/example/jobs/457', 'https://evil.test/embed/example/jobs/456']) {
    await session.page.evaluate(target => { window.__remixContext.state.loaderData['routes/embed.job_app'].submitPath = target; }, target);
    const after = await dispatch(session, 'snapshot', {});
    assert.equal(after.submission_target, null);
    assert.notEqual(before.snapshot_id, after.snapshot_id);
  }
  const {passiveCaptchaBadge} = require('../browser/greenhouse.cjs');
  assert.equal(passiveCaptchaBadge({src: 'https://www.recaptcha.net/recaptcha/enterprise/anchor?size=invisible'}), true);
  assert.equal(passiveCaptchaBadge({src: 'https://www.recaptcha.net/recaptcha/enterprise/bframe'}), false);
  assert.equal(passiveCaptchaBadge({src: 'https://evil.test/recaptcha/enterprise/anchor?size=invisible'}), false);
}

async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-greenhouse-test-'));
  const session = await start({dataRoot: root, headless: true, allowLocal: true});
  try {
    await session.context.route('**/*', route => route.fulfill({body: html, contentType: 'text/html'}));
    const first = await dispatch(session, 'open', {url: 'https://job-boards.greenhouse.io/fixture'});
    assert.equal(first.fields.length, 2, 'Hidden validation proxy is not a second question');
    assert.deepEqual(first.fields.filter(f => f.type === 'file').map(f => [f.label, f.required]), [['Resume/CV*', true]]);
    const combo = first.fields.find(f => f.type === 'combobox');
    const filled = await dispatch(session, 'fill', {snapshot_id: first.snapshot_id, answers: [{field_id: combo.field_id, value: 'Yes'}]});
    assert.equal(filled.fields.find(f => f.field_id === combo.field_id).value, 'Yes');
    assert.equal(filled.readbacks[0].matches, true);
    const next = await dispatch(session, 'fill', {snapshot_id: filled.snapshot_id, answers: [{field_id: combo.field_id, value: 'No'}]});
    assert.equal(next.fields.find(f => f.field_id === combo.field_id).value, 'No');
    assert.equal(session.page.isClosed(), false);
    await destinationChecks(session);
    console.log('Greenhouse labels, validation proxy, exact dropdown selection and readback verified.');
  } finally { await session.context.close(); await fs.rm(root, {recursive: true, force: true}); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

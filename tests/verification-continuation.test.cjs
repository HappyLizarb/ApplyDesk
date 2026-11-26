const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');
const {continueVerification} = require('../browser/verification.cjs');

// A fictional intercepted employer exercises eight-cell filling, one continuation and receipt detection.
async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-code-'));
  const session = await start({dataRoot: root, applicationWorkspace: true, headless: true, allowLocal: true});
  try {
    const html = `<h1>Example Engineer</h1><form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;this.remove();document.body.insertAdjacentHTML('beforeend','<p>Thank you for applying</p>')">
      <label>Name<input value="Test Person"></label><fieldset><legend>A verification code was sent to applicant@example.test.</legend>
      <label for="c0">Security code</label>${Array.from({length:8},(_,i)=>`<input id="c${i}" type="text" maxlength="1" required>`).join('')}</fieldset>
      <button>Submit application</button></form><script>window.__remixContext={state:{loaderData:{'routes/embed.job_app':{submitPath:'https://boards.greenhouse.io/embed/example/jobs/123'}}}};</script>`;
    await session.context.route('**/*', route => route.fulfill({contentType: 'text/html', body: html}));
    const snapshot = await dispatch(session, 'open', {job_id: 'test', url: 'https://job-boards.greenhouse.io/embed/job_app?for=example&token=123'});
    session.tabs.get('test').state.submissionAttempt = 'original';
    await dispatch(session, 'pause', {job_id:'test'});
    const authorization = {job_id:'test',attempt_id:'original',snapshot,company:'Example',title:'Engineer',code_ids:snapshot.fields.filter(f=>f.group.includes('verification code')).map(f=>f.field_id)};
    await assert.rejects(continueVerification(session,{...authorization,attempt_id:'other'},'12345678'),/original pending/);
    await assert.rejects(continueVerification(session,{...authorization,snapshot:{...snapshot,snapshot_id:'stale'}},'12345678'),/changed/);
    const receipt=await continueVerification(session,authorization,'12345678');
    assert.equal(receipt.confirmed,true);assert.equal(receipt.attempt_id,'original');
    assert.equal(await session.page.evaluate(()=>window.sent),1);
    assert.equal(session.tabs.get('test').state.paused,true);
    await assert.rejects(continueVerification(session,authorization,'12345678'),/already attempted/);
    console.log('Verification fixture passed: exact retained attempt, stale audit rejected, eight cells, one submit, receipt, parked browser, duplicate blocked.');
  } finally {await session.context.close();await fs.rm(root,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

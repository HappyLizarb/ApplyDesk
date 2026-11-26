const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// This intercepted fictional form simulates an employer requesting an emailed verification code.
async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-verification-'));
  const session = await start({dataRoot: root, headless: true, allowLocal: true});
  try {
    await session.context.route('**/*', route => route.fulfill({contentType: 'text/html', body: `<title>Example Engineer</title>
      <h1>Example Engineer</h1><form onsubmit="event.preventDefault();document.querySelector('#verify').hidden=false;this.querySelector('button').disabled=true">
      <label>Name<input value="Test Person"></label><div id="verify" hidden><p>A verification code was sent to applicant@example.test. To submit your application, enter the code.</p>
      <label>Security code<input></label></div><button>Submit application</button></form>`}));
    const page = await dispatch(session, 'open', {url: 'https://employer.example/job'});
    const params = {snapshot_id: page.snapshot_id, field_id: page.buttons[0].field_id, attempt_id: 'one', company: 'Example', title: 'Engineer'};
    const result = await dispatch(session, 'submit', params);
    assert.equal(result.confirmed, false);
    assert.equal(result.verification_required, true);
    assert.match(result.issue, /verification code/);
    assert.equal(result.receipt, '');
    assert.equal(session.page.isClosed(), false);
    console.log('Email verification returns Needs input evidence without claiming submission.');
  } finally { await session.context.close(); await fs.rm(root, {recursive: true, force: true}); }
}
main().catch(error => {console.error(error);process.exitCode=1;});

const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start,dispatch} = require('../browser/worker.cjs');
const {DECLARATION} = require('../browser/submission.cjs');

// A declined recording option cannot hide another declaration or imply affirmative consent.
(async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-declarations-'));
  const session=await start({dataRoot:root,headless:true,allowLocal:true});
  try {
    await session.context.route('**/*',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:`
      <fieldset><label class="ashby-application-form-question-title">Interview Recording Consent</label>
      <label><input type="radio" name="recording" id="yes">Yes, I consent to be recorded</label>
      <label><input type="radio" name="recording" id="no" checked>Opt out of recording</label></fieldset>
      <p id="other">I certify my answers.</p>`}));
    let snapshot=await dispatch(session,'open',{url:'https://jobs.ashbyhq.com/example/job/application'});
    assert.equal(DECLARATION.test(snapshot.declaration_text),true);
    await session.page.$eval('#other',node=>node.remove());
    snapshot=await dispatch(session,'snapshot',{});
    assert.equal(DECLARATION.test(snapshot.text),true);
    assert.equal(DECLARATION.test(snapshot.declaration_text),false);
    await session.page.$eval('#yes',node=>{node.checked=true});
    snapshot=await dispatch(session,'snapshot',{});
    assert.equal(DECLARATION.test(snapshot.declaration_text),true);
    console.log('Declined recording is distinguished from accepted or unrelated declarations.');
  }finally{await dispatch(session,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// Local fixture reproduces observed markup; no employer receives this synthetic answer.
const html = `<form><fieldset><label class="ashby-application-form-question-title _required_fixture">Veteran Status</label>
<label><input type="radio" name="veteran">No</label><label><input type="radio" name="veteran">Yes</label></fieldset>
<div class="ashby-application-form-field-entry"><label class="ashby-application-form-question-title _required_fixture">Visa Sponsorship</label>
<div class="ashby-application-form-input-yesno"><button type="button" data-option="yes" aria-pressed="false">Yes</button>
<button type="button" data-option="no" aria-pressed="false">No</button><input type="checkbox" hidden></div></div><fieldset class="ashby-application-form-input-checkbox-group"><label class="ashby-application-form-question-title" for="race">Race</label><input type="checkbox" id="survey_race-labeled-checkbox-0" name="Asian"><label for="survey_race-labeled-checkbox-0">Asian</label></fieldset></form>
<script>for(const b of document.querySelectorAll('[data-option]'))b.onclick=()=>{for(const p of b.parentElement.querySelectorAll('button'))p.setAttribute('aria-pressed',String(p===b))}</script>`;
(async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-ashby-test-'));
  const session=await start({dataRoot:root,headless:true,allowLocal:true});
  try {
    await session.context.route('**/*',route=>route.fulfill({body:html,contentType:'text/html'}));
    const page=await dispatch(session,'open',{url:'https://jobs.ashbyhq.com/example/job/application'});
    assert.equal(page.fields.length,4);
    assert.equal(page.fields[0].group,'Veteran Status');
    assert.equal(page.fields[0].required,true);
    assert.deepEqual(page.fields.find(f=>f.label==='Asian').ashby_binding,{form:'survey',path:'race'});
    const field=page.fields.find(f=>f.widget==='ashby_boolean');
    assert.equal(field.required,true);
    let state=page;
    for(const value of ['no','yes']){
      state=await dispatch(session,'fill',{snapshot_id:state.snapshot_id,answers:[{field_id:field.field_id,value}]});
      assert.equal(state.fields.find(f=>f.field_id===field.field_id).value,value);
      assert.equal(state.readbacks[0].matches,true);
    }
    await session.page.setContent(`<div><h1>Production Editor at Example</h1><div class="ashby-job-posting-right-pane"><input aria-label="Name"><button class="ashby-application-form-submit-button">Submit Application</button></div></div>`);
    const panel=await dispatch(session,'snapshot',{});
    assert.ok(panel.buttons[0].context_id);
    assert.equal(panel.fields[0].context_id,panel.buttons[0].context_id);
    assert.match(panel.buttons[0].context_text,/Production Editor at Example/);
    await session.page.$eval('.ashby-job-posting-right-pane',node=>node.insertAdjacentHTML('beforeend','<button class="ashby-application-form-submit-button">Submit Application</button>'));
    const ambiguous=await dispatch(session,'snapshot',{});
    assert.equal(ambiguous.fields[0].context_id,null);
    console.log('Ashby required questions and yes/no readbacks verified.');
  } finally {await dispatch(session,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

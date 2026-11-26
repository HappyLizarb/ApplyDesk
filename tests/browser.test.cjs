const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {start, dispatch} = require('../browser/worker.cjs');
const {assertPublicUrl, privateAddress} = require('../browser/guard.cjs');

const fixture = `<!doctype html><title>Application fixture</title><main>
<label>First name <input name="first" value="Incorrect prefill"></label>
<label>Phone <input name="phone" type="tel"></label>
<label>Account password <input name="password" type="password"></label>
<fieldset><legend>Will you need sponsorship?</legend><label>Yes <input type="radio" name="sponsor" value="yes"></label><label>No <input type="radio" name="sponsor" value="no"></label></fieldset>
<label>Country<select name="country"><option value="">Choose</option><option value="US">United States</option><option value="CA">Canada</option></select></label>
<label>Resume<input name="resume" type="file"></label><label>Cover letter<input name="letter" type="file"></label>
<div role="combobox" aria-label="Unsupported widget" tabindex="0">Pick something</div>
<label>Custom country<input name="custom-country" type="text" role="combobox"></label>
<button type="button" id="next" onclick="fetch('/save-draft',{method:'POST',body:'private'}).catch(()=>{})">Next</button>
<button type="button" id="final" onclick="fetch('/submit',{method:'POST'}).catch(()=>{})">Submit application</button>
<form action="/submit" method="post"><button id="native">Continue</button></form>
<a href="/next-page">Apply now</a></main>`;

// A local fixture records actual requests, so a clicked button cannot count as success.
async function serverFixture() {
  const received = [];
  const server = http.createServer((request, response) => {
    if (request.method === 'POST') received.push(request.url);
    response.setHeader('Content-Type', 'text/html');
    if (request.url === '/login') return response.end('<title>Account</title><form action="/login" method="post"><label>Email<input name="email"></label><label>Password<input name="password" type="password"></label><button>Sign in</button></form>');
    response.end(fixture);
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return {server, received, url: `http://127.0.0.1:${server.address().port}`};
}

// Similar text inputs must retain distinct identities and exact readbacks.
async function testFields(session, url) {
  let snapshot = await dispatch(session, 'open', {url});
  const phone = snapshot.fields.find(field => field.name === 'phone');
  const first = snapshot.fields.find(field => field.name === 'first');
  assert.notEqual(phone.field_id, first.field_id);
  const radios = snapshot.fields.filter(field => field.type === 'radio');
  assert(radios[0].group_id && radios[0].group_id === radios[1].group_id);
  assert.equal(first.context_id, snapshot.buttons.find(button => button.text === 'Next').context_id);
  assert(snapshot.buttons.find(button => button.text === 'Continue').form_id);
  assert.equal(first.value, 'Incorrect prefill');
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: phone.field_id, value: '+14155550100'}]});
  assert.equal(snapshot.fields.find(field => field.name === 'first').value, 'Incorrect prefill');
  assert.equal(snapshot.readbacks[0].value, '+14155550100');
  assert(snapshot.fields.some(field => field.type === 'combobox' && !field.supported));
  assert.equal(snapshot.fields.find(field => field.name === 'custom-country').supported, false);
  assert(snapshot.links[0].id && snapshot.links[0].href);
  const password = snapshot.fields.find(field => field.type === 'password');
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: password.field_id, value: 'secret-test-password'}]});
  assert(!JSON.stringify(snapshot).includes('secret-test-password'));
  await session.page.evaluate(() => { document.querySelector('input[name="first"]').value = 'Changed'; });
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: first.field_id, value: 'Test'}]}), /form changed/);
  await session.page.evaluate(() => { document.querySelector('input[name="first"]').readOnly = true; });
  snapshot = await dispatch(session, 'snapshot', {});
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: first.field_id, value: 'Wrong'}]}), /Readonly field/);
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: first.field_id, value: 'Changed'}]});
  assert.equal(snapshot.readbacks[0].value, 'Changed');
}

// Invalid answers and changed document bytes are rejected before browser mutation.
async function testDocuments(session, url, directory) {
  let snapshot = await dispatch(session, 'open', {url});
  const country = snapshot.fields.find(field => field.type === 'select');
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: country.field_id, value: 'UK'}]}), /exact enabled option/);
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: country.field_id, value: 'United States'}]}), /exact enabled option/);
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: country.field_id, value: 'US'}]});
  assert.equal(snapshot.readbacks[0].value, 'US');
  const file = path.join(directory, 'resume.pdf');
  await fs.writeFile(file, 'actual document bytes');
  const field = snapshot.fields.find(item => item.name === 'resume');
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, uploads: [{field_id: field.field_id, path: file, sha256: 'wrong'}]}), /Document changed/);
  const sha256 = crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, uploads: [{field_id: field.field_id, path: file, sha256}]});
  assert.equal(snapshot.fields.find(item => item.name === 'resume').files[0].name, 'resume.pdf');
  assert.equal(snapshot.fields.find(item => item.name === 'resume').files[0].sha256, sha256);
  assert.equal(snapshot.fields.find(item => item.name === 'letter').files.length, 0);
  assert.equal(snapshot.readbacks[0].upload_confirmed, false);
  await session.page.evaluate(() => {
    const transfer = new DataTransfer(); transfer.items.add(new File(['different bytes here!'], 'resume.pdf'));
    document.querySelector('input[name="resume"]').files = transfer.files;
  });
  const swapped = await dispatch(session, 'snapshot', {});
  assert.notEqual(swapped.fields.find(item => item.name === 'resume').files[0].sha256, sha256);
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: []}), /form changed/);
}

// A changing form interrupts the batch before the next answer can go to a stale field.
async function testDynamic(session, url) {
  let snapshot = await dispatch(session, 'open', {url});
  await session.page.evaluate(() => document.querySelector('input[name="first"]').addEventListener('input', () => {
    const label = document.createElement('label'); label.innerHTML = 'New question<input name="new">'; document.body.append(label);
  }, {once: true}));
  snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [
    {field_id: snapshot.fields.find(field => field.name === 'first').field_id, value: 'Name'},
    {field_id: snapshot.fields.find(field => field.name === 'phone').field_id, value: '555'},
  ]});
  assert.match(snapshot.intervention, /form changed/);
  assert.equal(snapshot.readbacks.length, 1);
  assert.equal(snapshot.fields.find(field => field.name === 'phone').value, '');
}

// Replaced radio nodes are audited afresh; later fields in the stale batch stay untouched.
async function testReplacedRadio(session,url) {
  let snapshot=await dispatch(session,'open',{url});
  await session.page.evaluate(()=>document.querySelector('input[value="no"]').addEventListener('change',event=>{
    const replacement=event.target.cloneNode();replacement.checked=true;event.target.replaceWith(replacement);
  },{once:true}));
  const radio=snapshot.fields.find(field=>field.type==='radio'&&field.value==='no');
  snapshot=await dispatch(session,'fill',{snapshot_id:snapshot.snapshot_id,answers:[
    {field_id:radio.field_id,value:true},{field_id:snapshot.fields.find(field=>field.name==='phone').field_id,value:'555'}]});
  assert.equal(snapshot.intervention,'field_replaced_during_fill');
  assert.equal(snapshot.fields.find(field=>field.type==='radio'&&field.value==='no').checked,true);
  assert.equal(snapshot.fields.find(field=>field.name==='phone').value,'');
  assert.equal(snapshot.readbacks.length,0);
}

// Workday can replace a visible field after inspection but before the first action resolves it.
async function testReplacedBeforeFill(session,url) {
  let snapshot=await dispatch(session,'open',{url});
  const first=snapshot.fields.find(field=>field.name==='first'),phone=snapshot.fields.find(field=>field.name==='phone');
  const original=session.page.evaluateHandle.bind(session.page);
  session.page.evaluateHandle=async(fn,id)=>{
    if(id===first.field_id){
      session.page.evaluateHandle=original;
      await session.page.evaluate(()=>{const field=document.querySelector('input[name="first"]');field.replaceWith(field.cloneNode());});
    }
    return original(fn,id);
  };
  try {
    snapshot=await dispatch(session,'fill',{snapshot_id:snapshot.snapshot_id,answers:[
      {field_id:first.field_id,value:'Confirmed name'},{field_id:phone.field_id,value:'555'}]});
  } finally {session.page.evaluateHandle=original;}
  assert.equal(snapshot.intervention,'field_replaced_during_fill');
  assert.equal(snapshot.readbacks.length,0);
  const replacement=snapshot.fields.find(field=>field.name==='first');
  assert.notEqual(replacement.field_id,first.field_id);
  assert.equal(replacement.value,'Incorrect prefill');
  assert.equal(snapshot.fields.find(field=>field.name==='phone').value,'');
  snapshot=await dispatch(session,'fill',{snapshot_id:snapshot.snapshot_id,answers:[{field_id:replacement.field_id,value:'Confirmed name'}]});
  assert.equal(snapshot.readbacks[0].value,'Confirmed name');
}

// Neither a final button nor a deceptive Next-triggered POST can submit automatically.
async function testSubmissionGuard(session, fixtureServer) {
  let snapshot = await dispatch(session, 'open', {url: fixtureServer.url});
  const final = snapshot.buttons.find(button => button.text === 'Submit application');
  await assert.rejects(dispatch(session, 'next', {snapshot_id: snapshot.snapshot_id, field_id: final.field_id}), /submission boundary/);
  const unsafeNext = snapshot.buttons.find(button => button.text === 'Next');
  await assert.rejects(dispatch(session, 'next', {snapshot_id: snapshot.snapshot_id, field_id: unsafeNext.field_id}), /submission boundary/);
  await session.page.locator('#final').evaluate(element => element.remove());
  snapshot = await dispatch(session, 'snapshot', {});
  const native = snapshot.buttons.find(button => button.text === 'Continue');
  await assert.rejects(dispatch(session, 'next', {snapshot_id: snapshot.snapshot_id, field_id: native.field_id}), /Native form submission/);
  const next = snapshot.buttons.find(button => button.text === 'Next');
  snapshot = await dispatch(session, 'next', {snapshot_id: snapshot.snapshot_id, field_id: next.field_id});
  assert(snapshot.guard.blocked_requests.some(request => request.url.endsWith('/save-draft')));
  assert.equal(fixtureServer.received.length, 0);
  assert.equal(snapshot.guard.manual, false);
}

// Auth writes are restricted to the observed auth destination, never the application.
async function testAuthentication(session, fixtureServer) {
  let snapshot = await dispatch(session, 'open', {url: fixtureServer.url + '/login'});
  assert(snapshot.buttons[0].form_id);
  assert(snapshot.fields.every(field => field.form_id === snapshot.buttons[0].form_id));
  snapshot = await dispatch(session, 'authenticate', {snapshot_id: snapshot.snapshot_id, field_id: snapshot.buttons[0].field_id, intent: 'sign_in'});
  assert(fixtureServer.received.includes('/login'));
  assert.equal(snapshot.guard.manual, false);
}

// Hidden tenant controls invalidate auth even when associated from outside the form.
async function testHiddenAuthenticationScope(session, fixtureServer) {
  for (const [external, type] of [[false, 'hidden'], [true, 'hidden'], [true, 'email'], [true, 'text'], [false, 'password']]) {
    let snapshot = await dispatch(session, 'open', {url: fixtureServer.url + '/login'});
    assert.equal(snapshot.buttons[0].auth_form, true);
    const password = snapshot.fields.find(field => field.type === 'password');
    snapshot = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id,
      answers: [{field_id: password.field_id, value: 'synthetic-private-password'}]});
    const postsBefore = fixtureServer.received.length;
    await session.page.evaluate(({external, type}) => {
      const form = document.querySelector('form'); form.id = 'employer-login';
      const hidden = document.createElement('input');
      hidden.type = type; hidden.name = type === 'hidden' ? 'tenant' : 'username'; hidden.value = 'synthetic-private-tenant';
      hidden.style[type === 'text' ? 'visibility' : 'display'] = type === 'text' ? 'hidden' : 'none';
      if (external) hidden.setAttribute('form', form.id);
      (external ? document.body : form).append(hidden);
    }, {external, type});
    const changed = await dispatch(session, 'snapshot', {});
    assert.equal(changed.buttons[0].auth_form, false);
    assert(!changed.fields.some(field => ['tenant', 'username'].includes(field.name)));
    assert(!JSON.stringify(changed).includes('synthetic-private-tenant'));
    assert(!JSON.stringify(snapshot).includes('synthetic-private-password'));
    assert(!JSON.stringify(changed).includes('synthetic-private-password'));
    await assert.rejects(dispatch(session, 'authenticate', {snapshot_id: snapshot.snapshot_id,
      field_id: snapshot.buttons[0].field_id, intent: 'sign_in'}), /form changed/);
    await assert.rejects(dispatch(session, 'authenticate', {snapshot_id: changed.snapshot_id,
      field_id: changed.buttons[0].field_id, intent: 'sign_in'}), /verified authentication form/);
    assert.equal(fixtureServer.received.length, postsBefore);
  }
}

// Handoff is the only action that releases submission guards, and it disables automation.
async function testHandoff(session, fixtureServer) {
  await dispatch(session, 'open', {url: fixtureServer.url});
  const snapshot = await dispatch(session, 'focus', {manual: true});
  assert.equal(snapshot.guard.manual, true);
  await assert.rejects(dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: []}), /manual control/);
  await session.page.locator('#final').click();
  await session.page.waitForTimeout(200);
  assert(fixtureServer.received.includes('/submit'));
}

// No live employer site, personal data, API key, or submission is used in these tests.
async function main() {
  assert(privateAddress('127.0.0.1'));
  assert(privateAddress('::1'));
  assert(privateAddress('192.168.1.1'));
  assert(!privateAddress('8.8.8.8'));
  await assert.rejects(assertPublicUrl('http://127.0.0.1:8000'), /Private network/);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-browser-'));
  const fixtureServer = await serverFixture();
  const session = await start({dataRoot: path.join(directory, 'profile'), headless: true, allowLocal: true, uploadRoots: [directory]});
  try {
    await testFields(session, fixtureServer.url);
    await testDocuments(session, fixtureServer.url, directory);
    await testDynamic(session, fixtureServer.url);
    await testReplacedRadio(session, fixtureServer.url);
    await testReplacedBeforeFill(session, fixtureServer.url);
    await testSubmissionGuard(session, fixtureServer);
    await testAuthentication(session, fixtureServer);
    await testHiddenAuthenticationScope(session, fixtureServer);
    await testHandoff(session, fixtureServer);
    console.log('Browser guard tests passed: identity, readback, stale/dynamic forms, attachments, auth scope, blocked submission, manual handoff.');
  } finally {
    await session.context.close();
    await new Promise(resolve => fixtureServer.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

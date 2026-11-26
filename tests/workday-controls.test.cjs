const assert=require('node:assert/strict'),fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {start,dispatch}=require('../browser/worker.cjs');
const html=`<main><h1>Example hardware application</h1><div data-automation-id="formField-phoneType"><label>Phone Device Type<abbr>*</abbr></label><button type="button" name="phoneType" aria-haspopup="listbox" value="" onclick="openOptions(this)">Select One</button></div>
<div data-automation-id="formField-countryPhoneCode"><label>Country Phone Code<abbr>*</abbr></label><input aria-label="Country Phone Code"><ul role="listbox"><li role="option" data-automation-id="selectedItem"><p data-automation-id="promptOption">United States of America (+1)</p></li></ul></div></main><div role="listbox"><div role="option">Home</div><div role="option">Unrelated stale choice</div></div>
<script>function openOptions(button){const menu=document.createElement('div');menu.role='listbox';menu.id='options';button.setAttribute('aria-controls','options');button.setAttribute('aria-expanded','true');const placeholder=document.createElement('div');placeholder.role='option';placeholder.textContent='Loading';placeholder.setAttribute('aria-disabled','true');menu.append(placeholder);setTimeout(()=>{placeholder.remove();for(const text of ['Home','Home Cellular']){const option=document.createElement('div');option.role='option';option.textContent=text;option.onclick=()=>{button.value=text;button.textContent=text;menu.remove()};menu.append(option)}},200);document.body.append(menu)}document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelector('#options')?.remove()})</script>`;
(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-workday-controls-'));
 const s=await start({dataRoot:root,headless:true,allowLocal:true});
 try{
  await s.context.route('**/*',r=>r.fulfill({contentType:'text/html',body:html}));
  let view=await dispatch(s,'open',{url:'https://example.wd5.myworkdayjobs.com/en-US/Example/job/US/Engineer_JR1/apply/applyManually'});
  assert.equal(view.fields.length,3);const dropdown=view.fields.find(f=>f.widget==='workday_select'),phone=view.fields.find(f=>f.widget==='workday_phone_code');
  assert.equal(phone.value,'+1');assert.equal(dropdown.choices_unread,true);
  view=await dispatch(s,'read_choices',{snapshot_id:view.snapshot_id,field_id:dropdown.field_id});
  assert.deepEqual(view.fields.find(f=>f.field_id===dropdown.field_id).options.map(o=>o.value),['Home','Home Cellular']);
  const filled=await dispatch(s,'fill',{snapshot_id:view.snapshot_id,answers:[{field_id:dropdown.field_id,value:'Home Cellular'},{field_id:phone.field_id,value:'+1'}]});
  assert.equal(filled.fields.find(f=>f.field_id===dropdown.field_id).value,'Home Cellular');
  assert.equal(filled.readbacks.length,2);
  await assert.rejects(dispatch(s,'fill',{snapshot_id:filled.snapshot_id,answers:[{field_id:phone.field_id,value:'+44'}]}),/differs from the confirmed/);
  console.log('Workday choices are read before selecting; selected phone codes are checked without editing search inputs.');
 }finally{await dispatch(s,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});

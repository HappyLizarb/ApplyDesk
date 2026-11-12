import assert from 'node:assert/strict';
import {test,beforeEach,afterEach} from 'node:test';
import {state,toast,refresh} from '../web/ui.js';

let renderProfile,root,form,calls,server,respond,load=0;
const originalFormData=globalThis.FormData;

// Browser controls expose mutable values and listeners without requiring a DOM dependency.
function control(value='') {
  return {value,checked:false,disabled:false,textContent:'',innerHTML:'',tagName:'INPUT',
    addEventListener(){},setCustomValidity(){},classList:{toggle(){}},focus(){}};
}

// These facts independently describe old and new fields expected in the saved profile.
function sampleProfile() {
  return {identity:{legal_name:'Test Applicant',given_name:'Preserved hidden name',email:'test@example.invalid',
    phone:'',phone_country_code:'',street_address:'Saved street'},confirmed:true,revision:4,transcript_id:'transcript',
    education:[],experiences:[{title:'Design intern',company:'Example employer',job_type:'Internship',
      start:'2025-06',end:'2025-08',location:'Remote',details:'Designed an accessible form.'}],
    projects:[{name:'Course tracker',start:'2024',end:'Present',details:'Saved project facts',url:'https://example.invalid'}],
    languages:[{name:'French',proficiency:''},{name:'English',proficiency:'Native or bilingual proficiency'}],
    industries:[],websites:[],authorizations:[{country:'US',sponsorship_required:'no',status:'authorized_no_sponsor'}],
    skills:['Python','Writing'],preferences:{levels:['Internship'],disability:'decline',veteran:'unknown'}};
}

// FormData reads named controls exactly as profile.js does, including unchecked confirmation.
class FormValues {
  constructor(source){this.source=source;}
  has(name){return this.source.fields.has(name)&&(name!=='confirmed'||this.source.fields.get(name).checked);}
  get(name){return this.has(name)?this.source.fields.get(name).value:null;}
  getAll(name){return name==='levels'?this.source.levels:[];}
}

// Build only the public form surface needed to dispatch real profile event handlers.
function profileForm(profile) {
  const fields=new Map(),rows={};
  for(const [key,value] of Object.entries(profile.identity))if(key!=='given_name')fields.set('identity.'+key,control(value));
  for(const section of ['education','experiences','projects','languages','industries','websites','authorizations']){
    rows[section]=(profile[section]||[]).map((record,index)=>{
      for(const [key,value] of Object.entries(record))fields.set(`${section}.${index}.${key}`,control(value));
      const duration=control();
      return {querySelector:selector=>selector.includes('.start')?fields.get(`${section}.${index}.start`):
        selector.includes('.end')?fields.get(`${section}.${index}.end`):duration};
    });
  }
  for(const [name,value] of Object.entries({skills:profile.skills.join(', '),disability:profile.preferences.disability,
    veteran:profile.preferences.veteran,confirmed:'on'}))fields.set(name,control(value));
  fields.get('confirmed').checked=profile.confirmed;
  fields.get('experiences.0.start').value='Jun 2025';fields.get('experiences.0.end').value='Aug 2025';
  const submit={...control(),tagName:'BUTTON',textContent:'Save profile'},tags=control();
  const picker={open:false,contains:()=>false,querySelector:()=>control()};
  return {fields,rows,levels:profile.preferences.levels,handlers:{},isConnected:true,
    elements:{namedItem:name=>fields.get(name),confirmed:fields.get('confirmed')},
    addEventListener(type,handler){(this.handlers[type]??=[]).push(handler);},
    emit(type,event){this.handlers[type]?.forEach(handler=>handler(event));},
    querySelector:selector=>selector==='[type=submit]'?submit:selector==='.level-picker'?picker:tags,
    querySelectorAll:selector=>selector.startsWith('[data-row=')?rows[selector.slice(11,-2)]||[]:
      selector==='[name=levels]:checked'?profile.preferences.levels.map(value=>({value})):[]};
}

// Each test loads an independent draft module while retaining the real shared API functions.
beforeEach(async()=>{
  state.data={profile:sampleProfile(),profile_schema:2,jobs:[],profile_imports:[],chat_tasks:[]};state.view='profile';
  server=structuredClone(state.data);calls=[];form=profileForm(state.data.profile);
  const elements=Object.fromEntries(['#job-count','#profile-indicator','#toast'].map(id=>[id,control()]));
  globalThis.window={addEventListener(){},dispatchEvent(){}};
  globalThis.document={querySelector:selector=>elements[selector]};
  globalThis.FormData=FormValues;
  root={isConnected:true,markup:'',querySelector:()=>form,querySelectorAll:()=>[],
    set innerHTML(value){this.markup=value;form.handlers={};},get innerHTML(){return this.markup;}};
  respond=async(path,payload)=>{
    if(path==='/api/profile')server.profile={...structuredClone(payload),revision:5};
    return {ok:true,data:structuredClone(path==='/api/state'?server:server.profile)};
  };
  globalThis.fetch=async(path,options={})=>{
    const payload=options.body?JSON.parse(options.body):undefined;calls.push({path,payload});
    const result=await respond(path,payload);
    return {ok:result.ok,json:async()=>result.data};
  };
  ({renderProfile}=await import(`../web/profile.js?test=${++load}`));
  renderProfile(root);
});

afterEach(()=>{clearTimeout(toast.timer);globalThis.FormData=originalFormData;});

// Native form submission must carry new fields and preserve facts not currently rendered.
test('save preserves new profile fields, canonical months, and hidden identity facts',async()=>{
  const original=sampleProfile();
  form.emit('submit',{preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls.map(call=>call.path),['/api/profile','/api/state']);
  const saved=calls[0].payload;
  for(const section of ['experiences','projects','languages','skills','authorizations'])assert.deepEqual(saved[section],original[section]);
  assert.equal(saved.identity.given_name,'Preserved hidden name');
  assert.equal(saved.transcript_id,'transcript');
  assert.equal(saved.confirmed,true);
  assert.ok(root.innerHTML.indexOf('name="skills"')<root.innerHTML.indexOf('name="languages.0.name"'));
  assert.match(root.innerHTML,/name="languages\.0\.proficiency"><option value="">Select proficiency/);
});

// A failed save cannot erase the local draft or turn an edit into a confirmed fact.
test('rejected save retains edited values when the profile renders again',async()=>{
  form.fields.get('experiences.0.title').value='New unsaved title';
  form.fields.get('projects.0.start').value='Spring 2024';
  form.emit('input',{target:{name:'experiences.0.title'}});
  respond=async()=>({ok:false,data:{error:'Rejected field'}});
  form.emit('submit',{preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  renderProfile(root);
  assert.match(root.innerHTML,/value="New unsaved title"/);
  assert.match(root.innerHTML,/value="Spring 2024"/);
  assert.equal(calls[0].payload.confirmed,false);
  assert.equal(document.querySelector('#toast').textContent,'Rejected field');
});

// Input during an outstanding request belongs to the next save, not to the completed snapshot.
test('edits made during a save remain unsaved and survive rerendering',async()=>{
  let acknowledge;
  respond=(path,payload)=>path==='/api/profile'?new Promise(resolve=>{acknowledge=()=>{
    server.profile={...payload,revision:5};resolve({ok:true,data:structuredClone(server.profile)});
  };}):Promise.resolve({ok:true,data:server});
  form.emit('submit',{preventDefault(){}});
  form.fields.get('experiences.0.location').value='New location typed while saving';
  form.fields.get('languages.0.proficiency').value='Professional working proficiency';
  form.emit('input',{target:{name:'experiences.0.location'}});
  acknowledge();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[0].payload.experiences[0].location,'Remote');
  assert.equal(calls[0].payload.languages[0].proficiency,'');
  assert.equal(server.profile.confirmed,true);
  renderProfile(root);
  assert.match(root.innerHTML,/value="New location typed while saving"/);
  assert.match(root.innerHTML,/value="Professional working proficiency" selected/);
  assert.match(root.innerHTML,/>Draft<\/span>/);
  assert.equal(document.querySelector('#toast').textContent,'Profile saved. Your newer edits are still unsaved.');
});

// Rebuilding rows after Add must not discard other fields the user has not saved yet.
test('adding a language retains unsaved experience and project edits',()=>{
  form.fields.get('experiences.0.job_type').value='Part-time';
  form.fields.get('projects.0.details').value='Changed project details';
  const button={dataset:{add:'languages'}};
  form.emit('click',{target:{closest:()=>button}});
  assert.match(root.innerHTML,/value="Part-time"/);
  assert.match(root.innerHTML,/>Changed project details<\/textarea>/);
  assert.match(root.innerHTML,/name="languages\.2\.name"/);
  assert.deepEqual(calls,[]);
});

// An old Python process cannot silently discard fields supported only by newer JavaScript.
test('missing runtime schema prevents POST and retains the local draft',async()=>{
  delete state.data.profile_schema;
  form.fields.get('languages.0.name').value='German';
  form.emit('input',{target:{name:'languages.0.name'}});
  form.emit('submit',{preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls,[]);
  renderProfile(root);
  assert.match(root.innerHTML,/value="German"/);
  assert.match(document.querySelector('#toast').textContent,/full restart/);
  assert.doesNotMatch(document.querySelector('#toast').textContent,/saved/i);
});

// A nominally successful API response must prove that it retained every submitted row field.
test('a server response dropping languages preserves the draft without success',async()=>{
  form.fields.get('languages.0.name').value='German';
  form.fields.get('experiences.0.job_type').value='Part-time';
  form.emit('input',{target:{name:'languages.0.name'}});
  respond=async(path,payload)=>{
    const result={...structuredClone(payload),revision:5};delete result.languages;
    return {ok:true,data:result};
  };
  form.emit('submit',{preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(calls.map(call=>call.path),['/api/profile']);
  assert.equal(state.data.profile.revision,4);
  renderProfile(root);
  assert.match(root.innerHTML,/value="German"/);
  assert.match(root.innerHTML,/value="Part-time"/);
  assert.match(document.querySelector('#toast').textContent,/did not retain every profile field/);
  assert.doesNotMatch(document.querySelector('#toast').textContent,/saved/i);
});

// A response without a complete canonical profile or advancing revision cannot clear local edits.
test('malformed and unadvanced canonical responses retain the unsaved draft',async()=>{
  form.fields.get('projects.0.details').value='Keep these new project facts';
  form.emit('input',{target:{name:'projects.0.details'}});
  for(const changes of [{identity:null},{preferences:null},{skills:'wrong shape'},
    {confirmed:true},{revision:4}]){
    respond=async(path,payload)=>({ok:true,data:{...structuredClone(payload),revision:5,...changes}});
    form.emit('submit',{preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(state.data.profile.revision,4);
    assert.match(document.querySelector('#toast').textContent,/could not be verified/);
    renderProfile(root);
    assert.match(root.innerHTML,/>Keep these new project facts<\/textarea>/);
  }
  assert.ok(calls.every(call=>call.path==='/api/profile'));
});

// Real save handlers and an independently delayed poll must retain the canonical saved profile.
test('a late poll cannot erase the canonical save response after the draft clears',async()=>{
  const stale=structuredClone(server);let finishPoll,stateRequests=0;
  respond=async(path,payload)=>{
    if(path==='/api/profile'){
      server.profile={...structuredClone(payload),revision:5};
      return {ok:true,data:structuredClone(server.profile)};
    }
    if(++stateRequests===1)return new Promise(resolve=>{finishPoll=()=>resolve({ok:true,data:stale});});
    return {ok:true,data:structuredClone(server)};
  };
  form.fields.get('languages.0.name').value='German';
  form.emit('input',{target:{name:'languages.0.name'}});
  const poll=refresh();
  form.emit('submit',{preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.data.profile.revision,5);
  finishPoll();await poll;
  renderProfile(root);
  assert.equal(state.data.profile.languages[0].name,'German');
  assert.match(root.innerHTML,/value="German"/);
  assert.match(document.querySelector('#toast').textContent,/Changes saved/);
});

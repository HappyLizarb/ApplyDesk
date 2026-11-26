import {state,h,api,refresh,task,busy,toast,modal,upload} from './ui.js';
import {requestChat} from './chat.js';
import {deleteDocument} from './document-deletion.js';
import {profileListRow} from './profile-lists.js';
import {bindAuthorization} from './profile-authorization.js';
import {persistProfile} from './profile-persistence.js';
import {phoneFields,bindPhone} from './phone.js';
import {entryFields,timelineRow,storedDate,updateEntryDurations} from './profile-timeline.js';
import {readEducationDate,updateLevelTags,bindLevelPicker} from './profile-controls.js';

const sections={education:['Education',entryFields.education],experiences:['Professional experience',entryFields.experiences],languages:['Languages',['name','proficiency']],industries:['Roles and job levels',['name','years','levels']],websites:['Websites',['type','url']],authorizations:['Work authorization',['country','sponsorship_required','immigration_status','restrictions']]};
const labels={legal_name:'Legal name',family_name:'Family name',preferred_name:'Preferred name · optional',email:'Email',street_address:'Street address',phone_type:'Phone type',city:'City',region:'State or region',postal_code:'Postal code · optional',country:'Country or area'};
const addLabels={education:'education',experiences:'experience',languages:'language',industries:'role preference',websites:'website',authorizations:'work authorization'};
const confirmationTag='<span class="badge" data-profile-confirmation>Draft</span>';
const countries='AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ');
const regionNames=new Intl.DisplayNames(['en'],{type:'region'});
const countryOptions=[['','Select country or area'],...countries.map(code=>[code,regionNames.of(code)]).sort((a,b)=>a[1].localeCompare(b[1]))];
const disclosures=[['unknown','Not answered'],['yes','Yes'],['no','No'],['decline','Prefer not to disclose']];
let draft=null;
const imports=[];

// Native labeled controls keep the canonical profile editable without hidden guesses.
function field(name,key,value,options=null) {
  const label=h(labels[key]||key), id='profile-'+name.replaceAll('.','-');
  if(options)return `<label for="${id}">${label}<select id="${id}" name="${h(name)}">${options.map(([v,t])=>`<option value="${h(v)}" ${v===value?'selected':''}>${h(t)}</option>`).join('')}</select></label>`;
  const type=key==='email'?'email':'text';
  return `<label for="${id}">${label}<input id="${id}" name="${h(name)}" type="${type}" value="${h(value)}" maxlength="10000"></label>`;
}

// Repeated records match backend fields exactly; empty facts are never invented.
function rowMarkup(section,row,index) {
  if(entryFields[section])return timelineRow(section,row,index);
  return profileListRow(section,row,index,countryOptions);
}

// Repeated sections retain their labels and empty-state status.
function sectionMarkup(section,profile,embedded=false) {
  const [title]=sections[section];
  const heading=embedded?'h3':'h2';
  return `<section class="${embedded?'profile-detail-list':'panel'}"><div class="panel-heading"><div><${heading}>${title}</${heading}></div>${embedded?'':confirmationTag}</div><div>${(profile[section]||[]).map((row,index)=>rowMarkup(section,row,index)).join('')||'<p class="muted small">No entries.</p>'}</div><button type="button" class="quiet profile-add" data-add="${section}">Add ${addLabels[section]}</button></section>`;
}

// Preserve nonrendered identity facts while reading explicit choices from the form.
function readForm(form) {
  const result=structuredClone(draft||state.data.profile), values=new FormData(form);
  result.identity={street_address:'',postal_code:'',phone_country_code:'',...result.identity};
  result.identity=Object.fromEntries(Object.keys(result.identity).map(key=>[key,values.has('identity.'+key)?String(values.get('identity.'+key)||'').trim():result.identity[key]]));
  for(const [section,[,fields]] of Object.entries(sections)) {
    result[section]=[...form.querySelectorAll(`[data-row="${section}"]`)].map((_,index)=>readRow(form,values,section,index,fields));
  }
  result.authorizations.forEach((row,index)=>{
    const previous=(draft||state.data.profile).authorizations[index];
    row.status=previous?.country===row.country?(previous.status||'unknown'):'unknown';
  });
  result.skills=String(values.get('skills')||'').split(/[,\n]/).map(item=>item.trim()).filter(Boolean);
  result.preferences={levels:[...new Set(result.industries.flatMap(row=>row.levels))],disability:values.get('disability'),
    age_18_or_older:values.get('age_18_or_older')||'unknown',
    application_answers:[...new Set(String(values.get('application_answers')||'').split(/\r?\n/).map(line=>line.trim()).filter(Boolean))],
    veteran:values.get('veteran'),veteran_service:values.get('veteran_service')||'unknown',
    race:String(values.get('race')||'').trim(),hispanic:values.get('hispanic'),gender:String(values.get('gender')||'').trim()};
  result.confirmed=values.has('confirmed');
  return result;
}

// Read every visible row field while preserving the existing education date controls.
function readRow(form,values,section,index,fields) {
  const row={};
  for(const key of fields) {
    const name=`${section}.${index}.${key}`,value=String(values.get(name)||'').trim();
    if(key==='levels'){row[key]=values.getAll(name);continue;}
    const date=['start','end'].includes(key);
    row[key]=section==='education'&&date?readEducationDate(form,name):entryFields[section]&&date?storedDate(value):value;
  }
  return row;
}

// Imports remain separate from saved facts until the user explicitly merges a draft.
function importMarkup() {
  return `<section class="panel"><div class="panel-heading"><div><h2>Import profile</h2></div></div>
    <label>LinkedIn profile PDF<input type="file" data-import="profile" accept=".pdf,application/pdf"></label><div id="profile-imports">${imports.map((item,index)=>`<div class="file-row"><div class="file-info"><strong>${h(item.filename)}</strong><span>${item.needs_manual_entry?'No extractable text. Enter fields below.':'Stored locally'}</span></div><button type="button" data-extract="${index}" ${item.needs_manual_entry?'disabled':''}>Request extraction</button><button type="button" class="quiet danger" data-delete-upload="${h(item.id)}" aria-label="Delete ${h(item.filename)}">Delete</button></div>`).join('')}</div>${(state.data.chat_tasks||[]).filter(task=>task.kind==='profile'&&task.status==='draft_ready').map(task=>`<div class="file-row"><span>${h(task.title)}</span><button type="button" data-review-task="${h(task.id)}">Review extracted facts</button></div>`).join('')}
    </section>`;
}

// Confirmation is reset by edits, so a previous confirmation cannot approve new facts.
export function renderProfile(root) {
  if(state.data.profile_imports)imports.splice(0,imports.length,...state.data.profile_imports);
  const profile=draft||structuredClone(state.data.profile);
  profile.identity={street_address:'',postal_code:'',phone_country_code:'',...profile.identity};
  profile.industries=(profile.industries||[]).map(row=>({...row,levels:row.levels??profile.preferences.levels??[]}));
  if(!profile.industries.length&&profile.preferences.levels?.length)profile.industries=[{name:'',years:'',levels:profile.preferences.levels}];
  const identity=['legal_name','family_name','preferred_name','email','phone','phone_type','street_address','city','region','postal_code','country'].map(key=>key==='phone'?phoneFields(profile.identity):field('identity.'+key,key,profile.identity[key]||'',key==='country'?countryOptions:key==='phone_type'?[['','Not specified'],['mobile','Mobile'],['home','Home / landline']]:null)).join('');
  root.innerHTML=`<div class="page-head"><h1>Profile</h1><a class="small" href="/api/profile/export" download="applydesk-profile.json">Export JSON</a></div>
    <form id="profile-form">${importMarkup()}<section class="panel"><div class="panel-heading"><div><h2>Personal details</h2></div>${confirmationTag}</div>
    <div class="form-grid three">${identity}${field('age_18_or_older','Are you 18 or older?',profile.preferences.age_18_or_older||'unknown',[['unknown','Not answered'],['yes','Yes'],['no','No']])}</div>${sectionMarkup('websites',profile,true)}</section>${Object.keys(sections).filter(section=>!['languages','industries','websites'].includes(section)).map(section=>sectionMarkup(section,profile)).join('')}${preferencesMarkup(profile)}<div class="form-footer"><label class="check-label"><input type="checkbox" name="confirmed" ${profile.confirmed?'checked':''}>I reviewed these facts and confirm they are accurate.</label><button class="primary" type="submit">Save profile</button></div></form>`;
  const form=root.querySelector('#profile-form');
  bindLevelPicker(form);bindPhone(form);bindAuthorization(form);
  updateProfileBadges(form,profile.confirmed&&!draft);
  form.addEventListener('input',event=>captureProfileEdit(event,form));
  form.addEventListener('change',event=>captureProfileEdit(event,form));
  form.addEventListener('submit',event=>{event.preventDefault();saveProfile(form,root);});
  form.addEventListener('click',event=>handleProfileClick(event,form,root));
  form.querySelectorAll('[data-import]').forEach(input=>input.addEventListener('change',()=>importDocument(input,form,root)));
}

// Every major section shows saved confirmation; unsaved edits remain drafts until saved.
function updateProfileBadges(form,confirmed) {
  for(const badge of form.querySelectorAll('[data-profile-confirmation]')){
    badge.textContent=confirmed?'Confirmed':'Draft';
    badge.classList.toggle('ready',confirmed);
  }
}

// Capture changes once and update the visible controls without replacing focused fields.
function captureProfileEdit(event,form) {
  if(event.target.name==='veteran_service')updateVeteranFollowUp(form,event.target.value);
  if(event.target.name!=='confirmed')form.elements.confirmed.checked=false;
  draft=readForm(form);
  updateLevelTags(form);updateEntryDurations(form);
  updateProfileBadges(form,false);
}

// A non-veteran can answer once. Reopening the follow-up requires a fresh protected-status choice.
function updateVeteranFollowUp(form,service) {
  const followUp=form.querySelector('[data-protected-veteran]');
  const select=form.elements.namedItem('veteran');
  if(service==='no'&&select.value!=='decline')select.value='no';
  if(service!=='no'&&followUp.hidden&&select.value==='no')select.value='unknown';
  followUp.hidden=service==='no';
}

// Sensitive answers are optional and excluded from document and eligibility prompts.
function preferencesMarkup(profile) {
  const languages=(profile.languages||[]).map(languageRow).join('')||'<p class="muted small">No languages added.</p>';
  return `<section class="panel"><div class="panel-heading"><div><h2>Skills and preferences</h2></div>${confirmationTag}</div>
    <div class="form-grid"><label class="wide">Skills<textarea name="skills" rows="3" placeholder="Python, project management, financial modeling">${h((profile.skills||[]).join(', '))}</textarea></label>
    <label class="wide">Application answers<textarea name="application_answers" rows="3" placeholder="One answer per line">${h((profile.preferences.application_answers||[]).join('\n'))}</textarea></label></div><fieldset class="profile-languages"><legend>Languages</legend>${languages}<button type="button" class="quiet profile-add" data-add="languages">Add language</button></fieldset>${sectionMarkup('industries',profile,true)}</section>
    <section class="panel"><div class="panel-heading"><div><h2>Optional disclosures</h2></div>${confirmationTag}</div>
    <div class="form-grid">${field('disability','Disability',profile.preferences.disability,disclosures)}
    ${field('veteran_service','Are you a veteran?',profile.preferences.veteran_service||'unknown',disclosures)}
    <div data-protected-veteran ${profile.preferences.veteran_service==='no'?'hidden':''}>${field('veteran','Protected veteran status',profile.preferences.veteran,disclosures)}</div>${field('race','Race',profile.preferences.race||'')}${field('hispanic','Hispanic / Latino',profile.preferences.hispanic||'unknown',disclosures)}${field('gender','Gender',profile.preferences.gender||'')}</div></section>`;
}

// Proficiency stays optional and user-supplied; an import can retain its original wording.
function languageRow(row,index) {
  const choices=['Elementary proficiency','Limited working proficiency','Professional working proficiency','Full professional proficiency','Native or bilingual proficiency'];
  if(row.proficiency&&!choices.includes(row.proficiency))choices.push(row.proficiency);
  return `<div class="language-row" data-row="languages"><label class="sr-only" for="language-${index}">Language</label><input id="language-${index}" name="languages.${index}.name" value="${h(row.name||'')}" placeholder="Language" maxlength="500"><span aria-hidden="true">·</span><label class="sr-only" for="proficiency-${index}">Proficiency</label><select id="proficiency-${index}" name="languages.${index}.proficiency"><option value="">Select proficiency</option>${choices.map(value=>`<option value="${h(value)}" ${value===row.proficiency?'selected':''}>${h(value)}</option>`).join('')}</select><button type="button" class="row-remove quiet danger" data-remove="languages" data-index="${index}" aria-label="Remove language entry ${index+1}">Remove</button></div>`;
}

// Removing and adding rows preserve every other unsaved form value.
function handleProfileClick(event,form,root) {
  const button=event.target.closest('button');
  if(!button)return;
  if(button.dataset.add||button.dataset.remove) {
    draft=readForm(form);draft.confirmed=false;
    if(button.dataset.add)(draft[button.dataset.add]??=[]).push(Object.fromEntries(sections[button.dataset.add][1].map(key=>[key,key==='levels'?[]:''])));
    else draft[button.dataset.remove].splice(Number(button.dataset.index),1);
    renderProfile(root);
  }
  if(button.dataset.deleteUpload)deleteDocument(button.dataset.deleteUpload,()=>{
    if(form.isConnected)draft=readForm(form);
    if(root.isConnected&&state.view==='profile')renderProfile(root);
  });
  if(button.dataset.extract!==undefined)busy(button,async()=>{
    await requestChat('profile',imports[Number(button.dataset.extract)].id);
  });
  if(button.dataset.reviewTask)busy(button,async()=>{
    const context=await api('chat/task?id='+encodeURIComponent(button.dataset.reviewTask));
    if(context.task.status!=='draft_ready')throw new Error('Inputs changed. Ask the agent to prepare the extraction again.');
    showDraft(context.task.result.profile,readForm(form),root);
  });
}

// Profile saves are explicit and retain a draft if the local API rejects a field.
async function saveProfile(form,root) {
  await busy(form.querySelector('[type=submit]'),async()=>{
    const submitted=readForm(form),payload=structuredClone(submitted);draft=submitted;
    for(const section of Object.keys(sections))payload[section]=payload[section].filter(row=>Object.entries(row).some(([key,value])=>key!=='status'&&(Array.isArray(value)?value.length:value)));
    const saved=await persistProfile(payload,sections);await refresh();
    const current=form.isConnected?readForm(form):draft;
    if(JSON.stringify(current)!==JSON.stringify(submitted)){
      draft=current;toast('Profile saved. Your newer edits are still unsaved.');return;
    }
    draft=null;
    if(root.isConnected&&state.view==='profile')renderProfile(root);
    toast(saved.confirmed?'Profile confirmed and saved.':'Changes saved. Confirm your profile when ready to prepare applications.');
  });
}

// Upload completion never overwrites form edits while a background task is running.
async function importDocument(input,form,root) {
  if(!input.files[0])return;
  draft=readForm(form);input.disabled=true;
  try {
    const record=await upload(input.files[0],input.dataset.import);
    imports.push(record);
    if(form.isConnected){draft=readForm(form);renderProfile(root);}
    toast('Uploaded. Request extraction to prepare a profile draft.');
  } catch(error){toast(error.message,true);}
  finally {input.disabled=false;input.value='';}
}

// Each suggested value has a checkbox; conflicting existing identity values start unchecked.
function suggestionsFor(extracted,current) {
  const suggestions=[];
  for(const key of Object.keys(current.identity)) {
    if(key==='given_name')continue;
    const value=extracted.identity?.[key];
    if(value&&value!==current.identity[key])suggestions.push({label:labels[key],value,checked:!current.identity[key],current:current.identity[key],apply:result=>result.identity[key]=value});
  }
  for(const [section,[title,fields]] of Object.entries(sections)) {
    if(section==='authorizations')continue;
    for(const value of extracted[section]||[]) {
      const row=Object.fromEntries(fields.map(key=>[key,key==='levels'?(value[key]||[]):String(value[key]||'')]));
      if(!Object.values(row).some(value=>Array.isArray(value)?value.length:value)||(current[section]||[]).some(existing=>JSON.stringify(existing)===JSON.stringify(row)))continue;
      suggestions.push({label:title,value:Object.values(row).filter(Boolean).join(' · '),checked:true,apply:result=>(result[section]??=[]).push(row)});
    }
  }
  for(const skill of extracted.skills||[])if(!current.skills.includes(skill))suggestions.push({label:'Skill',value:skill,checked:true,apply:result=>result.skills.push(skill)});
  return suggestions;
}

// Merging creates an unconfirmed local draft; saving remains a separate user action.
function showDraft(extracted,current,root) {
  const suggestions=suggestionsFor(extracted,current);
  const body=suggestions.map((item,index)=>`<div class="row-editor"><label class="check-label"><input type="checkbox" data-suggestion="${index}" ${item.checked?'checked':''}><span><strong>${h(item.label)}</strong><br>${h(item.value)}${item.current?`<br><span class="muted">Current: ${h(item.current)}</span>`:''}</span></label></div>`).join('');
  const dialog=modal('Review extracted facts',`<div class="modal-body">${body||'<p>No new facts found.</p>'}</div>`,`<button type="button" id="merge-draft" class="primary" ${suggestions.length?'':'disabled'}>Add selected to draft</button>`);
  dialog.querySelector('#merge-draft').addEventListener('click',()=>{
    draft=structuredClone(current);
    dialog.querySelectorAll('[data-suggestion]:checked').forEach(input=>suggestions[Number(input.dataset.suggestion)].apply(draft));
    draft.confirmed=false;dialog.close();renderProfile(root);
    toast('Added to draft. Review and save.');
  });
}

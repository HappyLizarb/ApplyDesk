import {h,modal} from './ui.js';
import {levelPicker} from './profile-controls.js';

const jobLevels=['Internship','Entry level','Associate','Mid level','Senior','Lead','Manager','Director','Executive'];

// Compact rows keep native controls and unique labels without repeating visible headers.
export function profileListRow(section,row,index,countries) {
  if(section==='websites')return websiteRow(row,index);
  if(section==='industries')return roleRow(row,index);
  const prefix=`profile-authorizations-${index}`;
  const primary=`<label class="sr-only" for="${prefix}-country">Country or area</label><select id="${prefix}-country" name="authorizations.${index}.country">${countries.map(([value,label])=>`<option value="${h(value)}" ${value===row.country?'selected':''}>${h(label)}</option>`).join('')}</select>`;
  const sponsorship=row.sponsorship_required??({authorized_no_sponsor:'no',authorized_need_sponsor:'yes'}[row.status]||'');
  const choices=[['','Choose sponsorship'],['no','No sponsorship needed'],['yes','Sponsorship needed']];
  const secondary=`<label class="sr-only" for="${prefix}-sponsorship">If sponsorship is needed</label><select id="${prefix}-sponsorship" name="authorizations.${index}.sponsorship_required">${choices.map(([value,label])=>`<option value="${value}" ${value===sponsorship?'selected':''}>${label}</option>`).join('')}</select>`;
  return `<div class="profile-list-row" data-row="${section}"><div class="list-primary">${primary}</div><span aria-hidden="true">·</span><div class="list-secondary">${secondary}</div>${authorizationControls({...row,sponsorship_required:sponsorship},index)}<button type="button" class="list-remove quiet danger" data-remove="${section}" data-index="${index}" aria-label="Remove work authorization entry ${index+1}">Remove</button></div>`;
}

// Each role keeps its experience and sought seniority together, including custom saved levels.
function roleRow(row,index) {
  const prefix=`profile-industries-${index}`,selected=row.levels||[];
  const levels=[...jobLevels,...selected.filter(level=>!jobLevels.some(item=>item.toLowerCase()===level.toLowerCase()))];
  return `<div class="profile-list-row role-preference-row" data-row="industries"><div class="list-primary"><label class="sr-only" for="${prefix}-name">Industry or role</label><input id="${prefix}-name" name="industries.${index}.name" value="${h(row.name??'')}" placeholder="Industry or role" maxlength="10000"></div><span aria-hidden="true">·</span><div class="list-secondary"><label class="sr-only" for="${prefix}-years">Years of experience</label><input id="${prefix}-years" name="industries.${index}.years" value="${h(row.years??'')}" inputmode="decimal" size="${Math.max(2,String(row.years??'').length)}" placeholder="Years" maxlength="10000"><span>years</span></div><div class="role-levels">${levelPicker(levels,selected,`industries.${index}.levels`)}</div><button type="button" class="list-remove quiet danger" data-remove="industries" data-index="${index}" aria-label="Remove role preference entry ${index+1}">Remove</button></div>`;
}

// Website controls share the compact list layout without repeating visible field labels.
function websiteRow(row,index) {
  const prefix=`profile-websites-${index}`;
  const types=[['','Choose website type'],...['LinkedIn','Portfolio','GitHub','Handshake','Indeed'].map(type=>[type,type==='Portfolio'?'Personal / portfolio':type])];
  return `<div class="profile-list-row website-list-row" data-row="websites"><div class="list-primary"><label class="sr-only" for="${prefix}-type">Website type</label><select id="${prefix}-type" name="websites.${index}.type">${types.map(([value,label])=>`<option value="${value}" ${value===row.type?'selected':''}>${label}</option>`).join('')}</select></div><span aria-hidden="true">·</span><div class="list-secondary"><label class="sr-only" for="${prefix}-url">Website URL</label><input id="${prefix}-url" name="websites.${index}.url" type="url" value="${h(row.url||'')}" placeholder="https://" maxlength="10000"></div><button type="button" class="list-remove quiet danger" data-remove="websites" data-index="${index}" aria-label="Remove website entry ${index+1}">Remove</button></div>`;
}

const statuses=[['','Choose status'],['us_citizen','US citizen'],['us_national','US national'],
  ['permanent_resident','Permanent resident'],['f1_opt','F-1 OPT'],['f1_stem_opt','F-1 STEM OPT'],
  ['f1_cpt','F-1 CPT'],['other_work_authorized','Other work-authorized status'],['other_unsure','Other / unsure']];

// Hidden fields retain the user's explicit choices in the same unsaved profile draft.
function authorizationControls(row,index) {
  const active=row.country==='US'&&row.sponsorship_required==='no';
  const status=active?row.immigration_status||'':'',restrictions=active?row.restrictions||'':'';
  const label=statuses.find(([value])=>value===status)?.[1]||'';
  return `<input type="hidden" name="authorizations.${index}.immigration_status" value="${h(status)}"><input type="hidden" name="authorizations.${index}.restrictions" value="${h(restrictions)}"><button type="button" class="quiet authorization-status" data-us-status ${active?'':'hidden'}>${h(status?label:'Add US status')}</button>`;
}

// Country and sponsorship changes clear inactive details before the form reads its draft.
export function bindAuthorization(form) {
  form.addEventListener('change',event=>{
    if(!/^authorizations\.\d+\.(country|sponsorship_required)$/.test(event.target.name||''))return;
    const row=event.target.closest('[data-row="authorizations"]');
    const active=row.querySelector('[name$=".country"]').value==='US'&&row.querySelector('[name$=".sponsorship_required"]').value==='no';
    const button=row.querySelector('[data-us-status]');button.hidden=!active;
    if(active){showStatus(row);return;}
    row.querySelector('[name$=".immigration_status"]').value='';
    row.querySelector('[name$=".restrictions"]').value='';
    button.textContent='Add US status';
  });
  form.addEventListener('click',event=>{
    const button=event.target.closest('[data-us-status]');
    if(button)showStatus(button.closest('[data-row="authorizations"]'));
  });
}

// The popup collects self-reported facts without inferring legal permission or saving the profile.
function showStatus(row) {
  const status=row.querySelector('[name$=".immigration_status"]'),restrictions=row.querySelector('[name$=".restrictions"]');
  const body=`<div class="modal-body authorization-details"><label for="us-immigration-status">US immigration status<select id="us-immigration-status">${statuses.map(([value,label])=>`<option value="${value}" ${value===status.value?'selected':''}>${label}</option>`).join('')}</select></label><label for="us-work-restrictions">Status details and work restrictions (optional)<textarea id="us-work-restrictions" rows="3" maxlength="10000" placeholder="For example: field of study, employer, authorized dates or hours">${h(restrictions.value)}</textarea></label></div>`;
  const dialog=modal('US work authorization',body,'<button type="button" data-cancel-status>Cancel</button><button type="button" class="primary" data-use-status>Use status</button>','authorization-dialog');
  const choice=dialog.querySelector('#us-immigration-status'),apply=dialog.querySelector('[data-use-status]');
  apply.disabled=!choice.value;
  choice.addEventListener('change',()=>{apply.disabled=!choice.value;});
  dialog.querySelector('[data-cancel-status]').addEventListener('click',()=>dialog.close());
  apply.addEventListener('click',()=>{
    status.value=choice.value;restrictions.value=dialog.querySelector('#us-work-restrictions').value.trim();
    row.querySelector('[data-us-status]').textContent=statuses.find(([value])=>value===status.value)[1];
    dialog.close();status.dispatchEvent(new Event('change',{bubbles:true}));
  });
}

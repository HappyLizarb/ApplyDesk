import {h} from './ui.js';
import {authorizationControls} from './profile-authorization.js';
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

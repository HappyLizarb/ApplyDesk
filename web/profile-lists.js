import {h} from './ui.js';

// Compact rows keep native controls and unique labels without repeating visible headers.
export function profileListRow(section,row,index,countries) {
  const prefix=`profile-${section}-${index}`,isIndustry=section==='industries';
  const primary=isIndustry?`<label class="sr-only" for="${prefix}-name">Industry</label><input id="${prefix}-name" name="industries.${index}.name" value="${h(row.name??'')}" placeholder="Industry" maxlength="10000">`:
    `<label class="sr-only" for="${prefix}-country">Country or area</label><select id="${prefix}-country" name="authorizations.${index}.country">${countries.map(([value,label])=>`<option value="${h(value)}" ${value===row.country?'selected':''}>${h(label)}</option>`).join('')}</select>`;
  const sponsorship=row.sponsorship_required??({authorized_no_sponsor:'no',authorized_need_sponsor:'yes'}[row.status]||'');
  const choices=[['','Choose sponsorship'],['no','No sponsorship needed'],['yes','Sponsorship needed']];
  const secondary=isIndustry?`<label class="sr-only" for="${prefix}-years">Years of experience</label><input id="${prefix}-years" name="industries.${index}.years" value="${h(row.years??'')}" inputmode="decimal" size="${Math.max(2,String(row.years??'').length)}" placeholder="Years" maxlength="10000"><span>years</span>`:
    `<label class="sr-only" for="${prefix}-sponsorship">If sponsorship is needed</label><select id="${prefix}-sponsorship" name="authorizations.${index}.sponsorship_required">${choices.map(([value,label])=>`<option value="${value}" ${value===sponsorship?'selected':''}>${label}</option>`).join('')}</select>`;
  const label=isIndustry?'industry':'work authorization';
  return `<div class="profile-list-row" data-row="${section}"><div class="list-primary">${primary}</div><span aria-hidden="true">·</span><div class="list-secondary">${secondary}</div><button type="button" class="list-remove quiet danger" data-remove="${section}" data-index="${index}" aria-label="Remove ${label} entry ${index+1}">Remove</button></div>`;
}

import {h,modal} from './ui.js';
import {callingRegions} from './calling-regions.js';

const countries='AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ');
const regionNames=new Intl.DisplayNames(['en'],{type:'region'});
export const countryOptions=[['','Select country or area'],...countries.map(code=>[code,regionNames.of(code)]).sort((a,b)=>a[1].localeCompare(b[1]))];
const jobLevels=['Internship','Entry level','Associate','Mid level','Senior','Lead','Manager','Director','Executive'];

const sponsorshipChoices=[['','Choose sponsorship'],['no','No sponsorship needed'],['yes','Sponsorship needed']];
const websiteTypes=[['','Choose website type'],'LinkedIn',['Portfolio','Personal / portfolio'],'GitHub','Handshake','Indeed'];

// Option lists accept a plain value or an explicit [value, label] pair.
export function profileOptions(choices,selected) {
  return choices.map(choice=>{
    const [value,label]=Array.isArray(choice)?choice:[choice,choice];
    return `<option value="${h(value)}" ${value===selected?'selected':''}>${h(label)}</option>`;
  }).join('');
}

// Compact profile lists share the same editing layout and removal control.
export function profileListRow(section,row,index,entryLabel) {
  const prefix=`profile-${section}-${index}`,name=`${section}.${index}`;
  const control=(key,label,id=key)=>`id="${prefix}-${id}" name="${name}.${key}" aria-label="${label}"`;
  let primary,secondary,extra='',rowClass='';
  if(section==='relative_companies'){
    primary=`<input name="relative_companies" aria-label="Company name" value="${h(row)}" placeholder="Company name" maxlength="10000">`;
  }else if(section==='websites'){
    rowClass=' website-list-row';
    primary=`<select ${control('type','Website type')}>${profileOptions(websiteTypes,row.type)}</select>`;
    secondary=`<input ${control('url','Website URL')} type="url" value="${h(row.url)}" placeholder="https://" maxlength="10000">`;
  }else if(section==='industries'){
    rowClass=' role-preference-row';
    primary=`<input ${control('name','Industry or role')} value="${h(row.name)}" placeholder="Industry or role" maxlength="10000">`;
    secondary=`<input ${control('years','Years of experience')} value="${h(row.years)}" inputmode="decimal" size="${Math.max(2,String(row.years??'').length)}" placeholder="Years" maxlength="10000"><span>years</span>`;
    extra=`<div class="role-levels">${levelPicker(row.levels||[],name+'.levels')}</div>`;
  }else{
    const sponsorship=row.sponsorship_required??({authorized_no_sponsor:'no',authorized_need_sponsor:'yes'}[row.status]||'');
    primary=`<select ${control('country','Country or area')}>${profileOptions(countryOptions,row.country)}</select>`;
    secondary=`<select ${control('sponsorship_required','If sponsorship is needed','sponsorship')}>${profileOptions(sponsorshipChoices,sponsorship)}</select>`;
    extra=authorizationControls(row,index,row.country==='US'&&sponsorship==='no');
  }
  const secondaryField=secondary?`<span aria-hidden="true">·</span><div class="list-secondary">${secondary}</div>`:'';
  return `<div class="profile-list-row${rowClass}" data-row="${section}"><div class="list-primary">${primary}</div>${secondaryField}${extra}
    <button type="button" class="list-remove quiet danger" data-remove="${section}" data-index="${index}" aria-label="Remove ${entryLabel} entry ${index+1}">Remove</button></div>`;
}

// Native disclosure and checkboxes provide a keyboard-accessible multiple-selection dropdown.
function levelPicker(selected,name) {
  const selectedNames=new Set(selected.map(level=>level.toLowerCase()));
  const levels=[...jobLevels,...selected.filter(level=>!jobLevels.some(item=>item.toLowerCase()===level.toLowerCase()))];
  return `<details class="level-picker"><summary aria-label="Choose job levels sought"><span class="level-tags"></span><span aria-hidden="true">⌄</span></summary>
    <div class="level-menu">${levels.map(level=>`<label class="check-label"><input type="checkbox" name="${h(name)}" value="${h(level)}" ${selectedNames.has(level.toLowerCase())?'checked':''}>${h(level)}</label>`).join('')}</div></details>`;
}

// Proficiency stays optional and user-supplied; an import can retain its original wording.
export function languageRow(row,index) {
  const choices=['Elementary proficiency','Limited working proficiency','Professional working proficiency','Full professional proficiency','Native or bilingual proficiency'];
  if(row.proficiency&&!choices.includes(row.proficiency))choices.push(row.proficiency);
  return `<div class="language-row" data-row="languages"><label class="sr-only" for="language-${index}">Language</label><input id="language-${index}" name="languages.${index}.name" value="${h(row.name||'')}" placeholder="Language" maxlength="500"><span aria-hidden="true">·</span><label class="sr-only" for="proficiency-${index}">Proficiency</label><select id="proficiency-${index}" name="languages.${index}.proficiency"><option value="">Select proficiency</option>${profileOptions(choices,row.proficiency)}</select><button type="button" class="row-remove quiet danger" data-remove="languages" data-index="${index}" aria-label="Remove language entry ${index+1}">Remove</button></div>`;
}

const statuses=[['','Choose status'],['us_citizen','US citizen'],['us_national','US national'],
  ['permanent_resident','Permanent resident'],['f1_opt','F-1 OPT'],['f1_stem_opt','F-1 STEM OPT'],
  ['f1_cpt','F-1 CPT'],['other_work_authorized','Other work-authorized status'],['other_unsure','Other / unsure']];

// Hidden fields retain the user's explicit choices in the same unsaved profile draft.
function authorizationControls(row,index,active) {
  const status=active?row.immigration_status||'':'',restrictions=active?row.restrictions||'':'';
  const label=statuses.find(([value])=>value===status)?.[1]||'';
  return `<input type="hidden" name="authorizations.${index}.immigration_status" value="${h(status)}"><input type="hidden" name="authorizations.${index}.restrictions" value="${h(restrictions)}"><button type="button" class="quiet authorization-status" data-us-status ${active?'':'hidden'}>${h(status?label:'Add US status')}</button>`;
}

// Only the edited picker needs its selected tags refreshed.
function updateLevelTags(picker) {
  const selected=[...picker.querySelectorAll('input:checked')];
  picker.querySelector('.level-tags').innerHTML=selected.map(input=>`<span class="level-tag">${h(input.value)}</span>`).join('')||'Job levels sought';
}

// Country and sponsorship changes clear inactive details before the form reads its draft.
export function bindProfileControls(form) {
  bindPhone(form);
  const pickers=form.querySelectorAll('.level-picker');
  for(const picker of pickers)updateLevelTags(picker);
  form.addEventListener('keydown',event=>{
    const picker=event.target.closest?.('.level-picker');
    if(event.key==='Escape'&&picker?.open){picker.open=false;picker.querySelector('summary').focus();event.preventDefault();}
  });
  form.addEventListener('change',event=>{
    const picker=event.target.closest?.('.level-picker');
    if(picker)updateLevelTags(picker);
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
    for(const picker of pickers)if(!picker.contains(event.target))picker.open=false;
    const button=event.target.closest('[data-us-status]');
    if(button)showStatus(button.closest('[data-row="authorizations"]'));
  });
}

// The popup collects self-reported facts without inferring legal permission or saving the profile.
function showStatus(row) {
  const status=row.querySelector('[name$=".immigration_status"]'),restrictions=row.querySelector('[name$=".restrictions"]');
  const body=`<div class="modal-body authorization-details"><label for="us-immigration-status">US immigration status<select id="us-immigration-status">${profileOptions(statuses,status.value)}</select></label><label for="us-work-restrictions">Status details and work restrictions (optional)<textarea id="us-work-restrictions" rows="3" maxlength="10000" placeholder="For example: field of study, employer, authorized dates or hours">${h(restrictions.value)}</textarea></label></div>`;
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

// Resolve shared numbering regions once; menu order is independent of prefix-matching order.
const callingCodes=Object.keys(callingRegions).sort((a,b)=>b.length-a.length);
const callingOptions=Object.entries(callingRegions).map(([code,regions])=>{
  const names=regions.map(region=>region==='001'?'International services':regionNames.of(region));
  return [code,`${code} (${names.join(' / ')})`];
});

// Only ten-digit numbers without extensions or an international prefix receive the requested mask.
function formattedNumber(value) {
  if(!/^[\d\s()-]+$/.test(value))return value;
  const digits=value.replace(/\D/g,'');
  if(digits.length!==10)return value;
  return `(${digits.slice(0,3)})-${digits.slice(3,6)}-${digits.slice(6)}`;
}

// Split only an explicit prefix; the narrow closed control displays the code, with full menu labels.
export function phoneFields(identity) {
  let code=identity.phone_country_code||'',number=identity.phone||'';
  if(!code&&number.startsWith('+')){
    code=callingCodes.find(prefix=>number.startsWith(prefix))||'';
    if(code)number=number.slice(code.length).trimStart();
  }
  const options=!code||Object.hasOwn(callingRegions,code)?callingOptions:[...callingOptions,[code,`${code} (Other region)`]];
  const title=options.find(([value])=>value===code)?.[1]||'Calling code';
  return `<div class="phone-fields"><label class="phone-code" for="profile-phone-code">Calling code<select id="profile-phone-code" name="identity.phone_country_code" title="${h(title)}"><option value="">Code</option>${profileOptions(options,code)}</select><span class="phone-code-display" aria-hidden="true"><span>${h(code||'Code')}</span><span>⌄</span></span></label>
    <label for="profile-identity-phone">Phone<input id="profile-identity-phone" type="tel" name="identity.phone" value="${h(formattedNumber(number))}" autocomplete="tel-national" maxlength="500"></label></div>`;
}

// Preserve the caret's digit position while formatting typing, paste, and edits in the middle.
function bindPhone(form) {
  const input=form.elements.namedItem('identity.phone'),code=form.elements.namedItem('identity.phone_country_code');
  input.addEventListener('input',event=>{
    if(event.isComposing)return;
    const formatted=formattedNumber(input.value);
    if(formatted===input.value)return;
    const digitsBefore=input.value.slice(0,input.selectionStart).replace(/\D/g,'').length;
    input.value=formatted;
    let position=0,count=0;
    while(position<formatted.length&&count<digitsBefore){if(/\d/.test(formatted[position]))count++;position++;}
    input.setSelectionRange(position,position);
  });
  code.addEventListener('change',()=>{
    form.querySelector('.phone-code-display>span').textContent=code.value||'Code';
    code.title=code.value?code.selectedOptions[0].textContent:'Calling code';
  });
}

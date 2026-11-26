const {ashbyContext}=require('./ashby-fields.cjs');

// Education is one repeatable saved value; its child controls are not independent questions.
function ashbyEducationFields(root, identity, clean) {
  if (location.hostname !== 'jobs.ashbyhq.com') return [];
  const selector='[data-field-path="_systemfield_education_history"]';
  return [...root.querySelectorAll(selector)].filter(element=>element.getClientRects().length).map(element=>{
    const path=element.dataset.fieldPath, entry=element.dataset.fieldEntryId || '';
    const form=entry.endsWith('_'+path)?entry.slice(0,-path.length-1):'';
    const title=element.querySelector(':scope > .ashby-application-form-question-title');
    const rows=[...element.querySelectorAll('.ashby-application-form-input-education-entry')];
    const context=ashbyContext(element)||element.closest('form,section,main,[role="form"],[role="dialog"]');
    const parts=['degree','major','startDate','endDate','isCurrent'];
    const valid=rows.length>0&&rows.every(row=>row.querySelectorAll('input[role="combobox"]').length===1
      && [...row.querySelectorAll('input,select,textarea,[contenteditable="true"]')].every(control=>
        control.matches('input[role="combobox"]') || control.type==='checkbox'&&control.id===path+'-isCurrent'
        || control.type==='text'&&['degree','major'].some(part=>control.id===path+'-'+part)
        || control.tagName==='SELECT'&&['startDate','endDate'].some(part=>
          control.closest(`[id="${path}-${part}"]`)?.querySelectorAll('select').length===2)));

    return {field_id:identity(element),label:clean(title?.innerText),type:'education_history',widget:'ashby_education',
      required:!!title&&[...title.classList].some(name=>name.startsWith('_required_')),
      supported:!!form&&valid,readonly:false,disabled:!rows.length,ashby_binding:{form,path},
      education_fields:parts.filter(part=>rows.every(row=>row.querySelector(`[id="${path}-${part}"]`))),
      value:ashbyEducationRows(element).map(row=>Object.fromEntries(Object.entries(row).filter(([key,value])=>value!==''&&(key!=='isCurrent'||value||row.endDate)))),
      can_add:!!element.querySelector('.ashby-application-form-input-education-add:not(:disabled)'),
      form_id:element.closest('form')?identity(element.closest('form')):null,context_id:context?identity(context):null};
  });
}

// Return only explicit month/year selections; the site's internal default day is not a profile fact.
function ashbyEducationDate(row,id) {
  const selects=[...row.querySelectorAll(`[id="${id}"] select`)];
  if (selects.length!==2 || !selects[0].value || !selects[1].value) return '';
  const [month,year]=selects.map(select=>select.value);
  return /^\d{4}$/.test(year)&&/^(?:[1-9]|1[0-2])$/.test(month)?`${year}-${month.padStart(2,'0')}`:'';
}

// Suppress nested education inputs only when the caller also exposes the compound field above.
function ashbyEducationProxy(element) {
  if (location.hostname !== 'jobs.ashbyhq.com') return false;
  const container=element.closest('[data-field-path="_systemfield_education_history"]');
  return !!container&&!!container.querySelector('.ashby-application-form-input-education-entry');
}

// Read each repeated row within its own container because the site reuses child HTML IDs.
function ashbyEducationRows(root) {
  const path='_systemfield_education_history';
  return [...root.querySelectorAll('.ashby-application-form-input-education-entry')].map(row=>({
    schoolName:row.querySelector('input[role="combobox"]')?.value || '',
    degree:row.querySelector(`[id="${path}-degree"]`)?.value || '',
    major:row.querySelector(`[id="${path}-major"]`)?.value || '',
    startDate:ashbyEducationDate(row,`${path}-startDate`),endDate:ashbyEducationDate(row,`${path}-endDate`),
    isCurrent:!!row.querySelector(`[id="${path}-isCurrent"]`)?.checked}));
}
module.exports={ashbyEducationFields,ashbyEducationDate,ashbyEducationProxy,ashbyEducationRows};

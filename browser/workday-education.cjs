// Education rows have generated IDs; their section and labelled row groups remain stable.
function workdayEducationProxy(element) {
  return /^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname)
    && !!element.closest('[role="group"][aria-labelledby="Education-section"]');
}

// Treat each visible degree as a separate record, including its actual or expected end year.
function workdayEducationRows(root) {
  return [...root.querySelectorAll('[role="group"][aria-labelledby^="Education-"][aria-labelledby$="-panel"]')].map(row=>{
    const text=name=>row.querySelector(`input[name="${name}"]`)?.value || '';
    const year=name=>row.querySelector(`[data-automation-id="formField-${name}"] input[role="spinbutton"]`)?.value || '';
    const degree=row.querySelector('button[name="degree"][aria-haspopup="listbox"]');
    const selected=[...row.querySelectorAll('[data-automation-id="formField-fieldOfStudy"] [data-automation-id="promptOption"]')]
      .filter(option=>!option.closest('[data-automation-id="promptLeafNode"]'));
    return {schoolName:text('schoolName'),degree:degree?.value?degree.innerText.trim():'',
      fieldOfStudy:selected.length===1?(selected[0].getAttribute('data-automation-label')||selected[0].innerText).trim():'',
      gradeAverage:text('gradeAverage'),firstYearAttended:year('firstYearAttended'),lastYearAttended:year('lastYearAttended')};
  });
}

// Fail closed when a tenant adds a different control or a multiple-selection study field.
function workdayEducationSupported(row) {
  const names=['schoolName','degree','fieldOfStudy','gradeAverage','firstYearAttended','lastYearAttended'];
  const containers=[...row.querySelectorAll('[data-automation-id^="formField-"]')];
  if(containers.some(node=>!names.includes(node.dataset.automationId.slice(10))))return false;
  for(const name of ['schoolName','degree','fieldOfStudy','firstYearAttended','lastYearAttended']) {
    if(row.querySelectorAll(`[data-automation-id="formField-${name}"]`).length!==1)return false;
  }
  return row.querySelectorAll('input[name="schoolName"]').length===1
    && row.querySelectorAll('button[name="degree"][aria-haspopup="listbox"]').length===1
    && row.querySelectorAll('[data-automation-id="formField-fieldOfStudy"] input[data-uxi-widget-type="selectinput"]').length===1
    && ['firstYearAttended','lastYearAttended'].every(name=>row.querySelectorAll(`[data-automation-id="formField-${name}"] input[role="spinbutton"]`).length===1)
    && /To \(Actual or Expected\)/.test(row.querySelector('[data-automation-id="formField-lastYearAttended"]')?.innerText||'');
}

// One compound identity prevents searches and generic Year inputs from becoming unrelated answers.
function workdayEducationFields(root,identity,clean) {
  if(!/^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname))return [];
  return [...root.querySelectorAll('[role="group"][aria-labelledby="Education-section"]')]
    .filter(section=>section.getClientRects().length).map(section=>{
      const rows=[...section.querySelectorAll('[role="group"][aria-labelledby^="Education-"][aria-labelledby$="-panel"]')];
      const context=section.closest('form,main,[role="main"]');
      return {field_id:identity(section),type:'education_history',widget:'workday_education',label:'Education',
        required:!!section.querySelector('abbr'),supported:rows.length>0&&rows.every(workdayEducationSupported),
        disabled:false,readonly:false,value:workdayEducationRows(section),
        education_fields:['schoolName','degree','fieldOfStudy','gradeAverage','firstYearAttended','lastYearAttended'],
        end_date_kind:'actual_or_expected',form_id:null,context_id:context?identity(context):null};
    });
}
module.exports={workdayEducationProxy,workdayEducationRows,workdayEducationSupported,workdayEducationFields};

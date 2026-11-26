const {workdayEducationRows,workdayEducationSupported}=require('./workday-education.cjs');
const {fillStudy}=require('./workday-education-prompt.cjs');
const {setWorkdayControl}=require('./workday-controls.cjs');
const {isDeepStrictEqual}=require('node:util');
const SECTION='[role="group"][aria-labelledby="Education-section"]';
const ROW='[role="group"][aria-labelledby^="Education-"][aria-labelledby$="-panel"]';
const PARTS=['schoolName','degree','fieldOfStudy','gradeAverage','firstYearAttended','lastYearAttended'];

// Scope all controls to the inspected section; never delete pre-existing unmatched records.
async function educationRoot(page,field,answer) {
  if(field.widget!=='workday_education'||!Array.isArray(answer)||!answer.length||answer.length>20)
    throw new Error('The planned education records are invalid.');
  for(const row of answer) {
    if(!row||Object.keys(row).some(key=>!PARTS.includes(key))||PARTS.some(key=>typeof row[key]!=='string')
      ||!row.schoolName||!row.degree||!row.fieldOfStudy||!/^\d{4}$/.test(row.firstYearAttended)
      ||!/^\d{4}$/.test(row.lastYearAttended)||row.firstYearAttended>row.lastYearAttended)
      throw new Error('Education requires saved institution, degree, study field and valid years.');
  }
  const root=page.locator(SECTION);
  if(await root.count()!==1||!await root.evaluate((element,id)=>element===globalThis.__applyDesk.elements.get(id),field.field_id))
    throw new Error('The inspected education section changed.');
  if(await root.locator(ROW).count()>answer.length)throw new Error('Extra existing education records need review.');
  return root;
}

// Adding a visible row may fetch its definition; it does not submit the application.
async function ensureEducationRows(root,count,check) {
  while(await root.locator(ROW).count()<count) {
    const current=await root.locator(ROW).count(),add=root.locator('button[data-automation-id="add-button"]');
    if(await add.count()!==1||!await add.isEnabled())throw new Error('Another education row cannot be added.');
    check();await add.click({signal:check.signal});
    await root.locator(ROW).nth(current).waitFor({state:'visible',timeout:7000});
    await root.locator(ROW).nth(current).locator('input[name="schoolName"]').waitFor({state:'visible',timeout:7000});
  }
  if(!await root.locator(ROW).evaluateAll(new Function('rows',`const workdayEducationSupported=${workdayEducationSupported};return rows.every(workdayEducationSupported)`)))
    throw new Error('The education row contains unsupported or changed controls.');
}

// Text and year edits commit through native blur and then independently read their displayed value.
async function fillEducationText(row,name,value,check) {
  const selector=name.endsWith('YearAttended')?`[data-automation-id="formField-${name}"] input[role="spinbutton"]`:`input[name="${name}"]`;
  const input=row.locator(selector);
  if(!value&&await input.count()===0)return;
  if(await input.count()!==1)throw new Error('The education control is missing: '+name);
  if(await input.inputValue()!==value) {
    check();await input.fill(value,{signal:check.signal});await input.press('Tab');
  }
  if(await input.inputValue()!==value)throw new Error('Education readback failed: '+name);
}

// Degree selection must find the exact planned label in the live button's bound popup.
async function fillEducationDegree(page,row,value,check) {
  const button=row.locator('button[name="degree"][aria-haspopup="listbox"]');
  if(await button.count()!==1)throw new Error('The education degree control changed.');
  const element=await button.elementHandle();
  await setWorkdayControl(page,element,{widget:'workday_select',options:[{value,disabled:false}]},value,check);
  if(!await button.getAttribute('value'))throw new Error('The degree has no canonical selection identifier.');
}

// Fill through visible controls and verify every row after the two saved degrees are entered.
async function fillWorkdayEducation(page,state,field,answer,check) {
  const root=await educationRoot(page,field,answer),expected=structuredClone(answer);
  await ensureEducationRows(root,answer.length,check);
  for(let index=0;index<answer.length;index++) {
    const row=root.locator(ROW).nth(index),target=answer[index];
    for(const name of ['schoolName','gradeAverage','firstYearAttended','lastYearAttended'])
      await fillEducationText(row,name,target[name],check);
    await fillEducationDegree(page,row,target.degree,check);
    expected[index].fieldOfStudy=await fillStudy(page,state,row,index,field,target.fieldOfStudy,check);
  }
  const actual=await root.evaluate(workdayEducationRows);
  check();
  if(!isDeepStrictEqual(actual,expected))throw new Error('Education readback differs from the intended records.');
  return actual;
}
module.exports={educationRoot,ensureEducationRows,fillEducationText,fillEducationDegree,fillWorkdayEducation};

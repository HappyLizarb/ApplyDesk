const {ashbyContext}=require('../browser/ashby-fields.cjs');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {ashbyEducationFields,ashbyEducationDate,ashbyEducationProxy,ashbyEducationRows}=require('../browser/ashby-education.cjs');
const path='_systemfield_education_history';
const rows=['Linguistics','Geography'].map(major=>`<div class="ashby-application-form-input-education-entry">
  <input role="combobox" value="Example University"><input id="${path}-degree" value="Bachelor of Arts">
  <input id="${path}-major" value="${major}"><div id="${path}-startDate"><select><option value="8">August</option></select><select><option value="2013">2013</option></select></div>
  <div id="${path}-endDate"><select disabled><option value="">Month</option></select><select disabled><option value="">Year</option></select></div>
  <input type="checkbox" id="${path}-isCurrent" checked></div>`).join('');
const html=`<main class="ashby-job-posting-right-pane"><div data-field-path="${path}" data-field-entry-id="fixture_${path}">
  <label class="ashby-application-form-question-title _required_fixture">Education History</label>${rows}
  <button class="ashby-application-form-input-education-add">+ Add Education</button></div><input id="unrelated"></main>`;
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage();
  await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('https://jobs.ashbyhq.com/example/job/application');
  const result=await page.evaluate(`(()=>{const ashbyContext=${ashbyContext};const ashbyEducationDate=${ashbyEducationDate};const ashbyEducationRows=${ashbyEducationRows};const fields=${ashbyEducationFields};const proxy=${ashbyEducationProxy};let id=0;const result=fields(document,()=>String(++id),value=>value?.trim()||'');return {fields:result,proxies:[...document.querySelectorAll('input,select')].map(proxy)}})()`);
  assert.equal(result.fields.length,1);const field=result.fields[0];
  assert.deepEqual(field.ashby_binding,{form:'fixture',path});
  assert.equal(field.value.length,2);assert.equal(field.required,true);
  assert.equal(field.value[1].major,'Geography');
  assert.equal(field.value[0].startDate,'2013-08');assert.equal(field.value[0].endDate,undefined);
  assert.equal(field.value[0].isCurrent,true);assert.equal(field.can_add,true);
  assert.equal(field.supported,true);
  assert(result.proxies.slice(0,-1).every(Boolean));assert.equal(result.proxies.at(-1),false);
  await page.goto('https://other.example.test/application');
  assert.equal(await page.evaluate(`(${ashbyEducationFields})(document,()=>'',value=>value).length`),0);
  console.log('Compound Ashby education metadata retains separate degrees and excludes unrelated controls.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {chromium}=require('playwright');
const metadata=require('../browser/workday-education.cjs');
const {fillWorkdayEducation}=require('../browser/workday-education-fill.cjs');
const {studyCandidates,uniqueStudyChoice}=require('../browser/workday-education-prompt.cjs');
const {browserGuard}=require('../browser/guard.cjs');
const pageUrl='https://example.wd5.myworkdayjobs.com/en-US/External/job/US/Engineer_JR1/apply/applyManually';

// Reproduce the public dropdown's bound canonical identifiers on the synthetic education markup.
function installControls() {
  const section=document.querySelector('[aria-labelledby="Education-section"]');
  globalThis.__applyDesk.elements.set('education',section);
  const configure=row=>{
    const degree=row.querySelector('button[name="degree"]');
    degree.onclick=()=>{
      const menu=document.createElement('div');menu.id=degree.id+'-menu';menu.role='listbox';
      degree.setAttribute('aria-controls',menu.id);degree.setAttribute('aria-expanded','true');
      const option=document.createElement('div');option.role='option';option.textContent='Bachelors';option.dataset.value='bachelors-id';
      option.onclick=()=>{degree.value='bachelors-id';degree.textContent='Bachelors';menu.remove();};menu.append(option);document.body.append(menu);
    };
    const input=row.querySelector('[data-automation-id="formField-fieldOfStudy"] input');
    input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();renderChoices(input);}};
    row.querySelectorAll('[data-automation-id="promptSearchButton"]').forEach(node=>node.remove());
    const search=document.createElement('button');search.type='button';search.dataset.automationId='promptSearchButton';search.textContent='Search';
    search.onclick=()=>renderChoices(input);input.closest('[data-automation-id="multiselectInputContainer"]').append(search);
  };
  configure(section.querySelector('[aria-labelledby="Education-1-panel"]'));
  section.querySelector('[data-automation-id="add-button"]').onclick=()=>{
    const first=section.querySelector('[aria-labelledby="Education-1-panel"]');
    const second=document.createElement('div');second.innerHTML=first.outerHTML.replaceAll('Education-1','Education-2').replaceAll('degree-1','degree-2').replaceAll('study-1','study-2');
    const row=second.firstElementChild;section.insertBefore(row,section.lastElementChild);configure(row);
  };
}

// Search responses arrive before matching visible leaf nodes and selected pills are rendered.
async function renderChoices(input) {
  document.querySelectorAll('[data-automation-id="promptLeafNode"]').forEach(node=>node.remove());
  const result=await fetch('/wday/calypso/cxs/jobapplication/example/values/educations/fieldsOfStudy?search='+encodeURIComponent(input.value)).then(response=>response.json());
  for(const choice of result) {
    const option=document.createElement('div');option.dataset.automationId='promptLeafNode';
    Object.assign(option.dataset,{uxiWidgetType:'multiselectlistitem',uxiMultiselectId:input.dataset.uxiMultiselectId,uxiMultiselectlistitemInstanceid:choice.id});
    const label=document.createElement('span');label.dataset.automationId='promptOption';label.dataset.automationLabel=choice.descriptor;label.textContent=choice.descriptor;
    option.append(label);option.onclick=()=>{
      const container=input.closest('[data-automation-id="formField-fieldOfStudy"]');
      container.querySelectorAll('[data-automation-id="selectedItem"]').forEach(node=>node.remove());
      const selected=document.createElement('li');selected.dataset.automationId='selectedItem';selected.append(label.cloneNode(true));container.append(selected);option.remove();input.value='';
    };document.body.append(option);
  }
}

// Metadata is evaluated in the page exactly as the application's inspection helper is embedded.
async function collect(page) {
  const helpers=Object.entries(metadata).map(([name,value])=>`const ${name}=${value};`).join('');
  return page.evaluate(new Function(`${helpers}return workdayEducationFields(document,()=> 'education',value=>String(value||'').trim());`));
}

async function run() {
  const fixture=await fs.readFile(require.resolve('./fixtures/workday-education.html'),'utf8');
  const browser=await chromium.launch({channel:'chrome',headless:true}),state={},queries=[];
  try {
    const page=await browser.newPage();await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.pathname.endsWith('/fieldsOfStudy')) {
        const query=url.searchParams.get('search');queries.push(query);
        return route.fulfill({json:['Geography','Textile Engineering'].includes(query)?[{id:query+'-id',descriptor:query}]:[]});
      }
      return route.fulfill({contentType:'text/html',body:'<main>'+fixture+'</main>'});
    });
    await page.goto(pageUrl);await page.evaluate(browserGuard,false);
    await page.evaluate(`const renderChoices=${renderChoices};(${installControls})();`);
    const [field]=await collect(page);assert(field.supported);assert(field.required);assert.equal(field.value.length,1);
    const values=['Geography & Library Sciences (Honours)','Textile Engineering Science'].map(fieldOfStudy=>({schoolName:'Example University',degree:'Bachelors',fieldOfStudy,gradeAverage:'',firstYearAttended:'2013',lastYearAttended:'2017'}));
    const result=await fillWorkdayEducation(page,state,field,values,()=>{});
    assert.equal(result.length,2);assert.deepEqual(result.map(row=>row.fieldOfStudy),['Geography','Textile Engineering']);
    assert.deepEqual((await collect(page))[0].value,result);assert.equal(state.workdayEducationChoices.size,2);
    const ambiguous=[{...values[0],fieldOfStudy:'Geography & Textile Engineering'},values[1]];
    await assert.rejects(fillWorkdayEducation(page,state,field,ambiguous,()=>{}),/Multiple field-of-study choices/);
    assert.deepEqual((await collect(page))[0].value,result);
    assert(queries.includes('Geography & Library Sciences (Honours)'));assert(queries.includes('Textile Engineering Science'));
    await assert.rejects(fillWorkdayEducation(page,state,field,[values[0]],()=>{}),/Extra existing education/);
    await page.locator('[data-automation-id="formField-lastYearAttended"]').first().evaluate(node=>node.querySelector('legend').textContent='Graduated');
    assert.equal((await collect(page))[0].supported,false);
    console.log('Workday education: education row metadata, two expected degrees, live-choice IDs, native values and changed-form rejection passed.');
  } finally {await browser.close();}
}
assert.equal(uniqueStudyChoice([{id:'one',descriptor:'Geography'}],'geography').id,'one');
assert.throws(()=>uniqueStudyChoice([{id:'one',descriptor:'Geography'},{id:'two',descriptor:'Geography'}],'Geography'),/multiple exact/);
assert.equal(uniqueStudyChoice([{id:'one',descriptor:'Classical Studies'}],'Geography'),null);
assert.deepEqual(studyCandidates('Engineering Science'),['Engineering Science']);
assert(studyCandidates('Geography & Library Sciences (Honours)').includes('Library Science'));
assert.deepEqual(studyCandidates('Textile Engineering Science'),['Textile Engineering Science','Textile Engineering']);
run().catch(error=>{console.error(error);process.exitCode=1;});

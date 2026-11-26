const {ashbyEducationDate,ashbyEducationRows}=require('./ashby-education.cjs');
const {findEducationSchool,saveEducationValue}=require('./ashby-education-network.cjs');
const ROW='.ashby-application-form-input-education-entry';
const PATH='_systemfield_education_history';
const KEYS=['canonicalSchoolId','schoolName','degree','major','startDate','endDate','isCurrent'];

// Match the latest acknowledged server array to visible rows before allowing another edit.
async function educationContext(page,state,field,answer) {
  const binding=field.ashby_binding,form=state.ashbyForms?.get(binding?.form),definition=form?.fields.get(PATH);
  if(field.widget!=='ashby_education'||binding?.path!==PATH||form?.pageUrl!==page.url()
      ||definition?.type!=='EducationHistory'||!Array.isArray(answer)||!answer.length)throw new Error('Education scope is missing or changed.');
  const selector=`[data-field-entry-id=${JSON.stringify(binding.form+'_'+PATH)}]`,root=page.locator(selector);
  if(await root.count()!==1)throw new Error('The education container is ambiguous.');
  const previous=state.ashbyEdits?.get(binding.form+':'+PATH);
  let values=previous?.saved?previous.value:form.values.get(PATH);
  const rows=await root.locator(ROW).count();
  // HBe renders initial blank rows for both null and an empty saved array.
  values=values==null||Array.isArray(values)&&!values.length?Array.from({length:rows},()=>({})):JSON.parse(JSON.stringify(values));
  if(!Array.isArray(values)||values.length!==rows||rows>answer.length||(!definition.isRepeatable&&answer.length!==1)
      ||definition.maxRepeat&&answer.length>definition.maxRepeat||values.some(row=>!row||Object.keys(row).some(key=>!KEYS.includes(key)))) {
    throw new Error('The existing education rows cannot be safely matched to the saved profile.');
  }
  for(const target of answer) {
    if(!target?.schoolName||Object.keys(target).some(key=>!KEYS.includes(key))||target.canonicalSchoolId
        ||target.isCurrent===true&&target.endDate)throw new Error('The planned education value is invalid.');
  }
  await verifyEducationRows(root,values);
  return {root,values};
}

// Compare actual controls independently from the server's acknowledgement, at month precision.
async function verifyEducationRows(root,values) {
  const readRows=new Function('root',`const ashbyEducationDate=${ashbyEducationDate};return (${ashbyEducationRows})(root)`);
  const rows=await root.evaluate(readRows);
  if(rows.length!==values.length)throw new Error('The education row count changed.');
  for(let index=0;index<rows.length;index++) {
    const expected=values[index],actual=rows[index];
    for(const key of ['schoolName','degree','major','startDate','endDate','isCurrent']) {
      const value=key==='isCurrent'?!!expected[key]:key.endsWith('Date')?(expected[key]||'').slice(0,7):expected[key]||'';
      if(actual[key]!==value)throw new Error('Education field readback did not match the saved value: '+key);
    }
  }
}

// Only the lookup's unique exact canonical name can select a school result.
async function selectEducationSchool(page,state,root,field,values,index,name,check) {
  const input=root.locator(ROW).nth(index).locator('input[role="combobox"]');
  if(values[index].canonicalSchoolId&&values[index].schoolName===name)return values;
  const school=await findEducationSchool(page,state,input,name,check);
  await input.waitFor({state:'visible',timeout:5000});
  const menuId=await input.getAttribute('aria-controls');
  if(!menuId)throw new Error('The school result menu is not bound to its input.');
  const menu=page.locator(`[id=${JSON.stringify(menuId)}][role="listbox"]`);
  const option=menu.getByRole('option').filter({has:page.getByText(school.name,{exact:true})});
  await option.waitFor({state:'visible',timeout:5000});
  if(await option.count()!==1)throw new Error('The school result is ambiguous.');
  const next=structuredClone(values);next[index]={...next[index],canonicalSchoolId:school.id,schoolName:school.name};
  return saveEducationValue(state,field,next,()=>option.click({signal:check.signal}),check);
}

// One text or checkbox change corresponds to one exact next compound array.
async function editEducationPart(page,state,root,field,values,index,part,value,check) {
  const control=root.locator(ROW).nth(index).locator(`[id="${PATH}-${part}"]`);
  if(values[index][part]===value)return values;
  if(await control.count()!==1)throw new Error('The requested education control is unavailable.');
  const next=structuredClone(values);next[index][part]=value;
  if(part==='isCurrent'&&value)delete next[index].endDate;
  const action=part==='isCurrent'?()=>control.setChecked(value,{signal:check.signal}):()=>control.fill(value,{signal:check.signal});
  const saved=await saveEducationValue(state,field,next,action,check);
  if(part!=='isCurrent')await flushEducationBlur(page,control,field,saved,check);
  return saved;
}

// The site's explicit text blur handler re-saves the same array; finish it before changing permits.
async function flushEducationBlur(page,control,field,values,check) {
  const response=page.waitForResponse(response=>{
    try {
      const body=response.request().postDataJSON(),data=body.variables;
      return body.operationName==='ApiSetFormValue'&&data.formRenderIdentifier===field.ashby_binding.form
        &&data.path===PATH&&JSON.stringify(data.value)===JSON.stringify(values);
    }catch{return false;}
  },{timeout:5000});
  response.catch(()=>{}); // A detached control must not leave an unhandled pending response rejection.
  check();await control.blur();
  const saved=await response;
  if(!saved.ok()||(await saved.json()).errors?.length)throw new Error('The education blur save was not confirmed.');
}

// Official LBe uses DateTime.utc().startOf('month'); match that fallback and verify each save.
async function editEducationDate(page,state,root,field,values,index,part,value,check) {
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(value))throw new Error('Education requires a confirmed month and year.');
  const selects=root.locator(ROW).nth(index).locator(`[id="${PATH}-${part}"] select`);
  if(await selects.count()!==2)throw new Error('The education date controls changed.');
  const target=value.split('-').map(Number);
  for(const [position,number] of [[1,target[0]],[0,target[1]]]) {
    if(await selects.nth(position).inputValue()===String(number))continue;
    const fallback=await page.evaluate(()=>new Date().toISOString().slice(0,7));
    const current=(values[index][part]||fallback).slice(0,7).split('-').map(Number);
    current[position===1?0:1]=number;
    const next=structuredClone(values);next[index][part]=`${current[0]}-${String(current[1]).padStart(2,'0')}-01`;
    values=await saveEducationValue(state,field,next,()=>selects.nth(position).selectOption(String(number)),check);
  }
  return values;
}

// Fill every profile degree through visible controls; never mutate React state or call a save API directly.
async function fillAshbyEducation(page,state,field,answer,check) {
  let {root,values}=await educationContext(page,state,field,answer);
  while(values.length<answer.length) {
    const add=root.locator('.ashby-application-form-input-education-add');
    if(await add.count()!==1||!await add.isEnabled())throw new Error('Another education row cannot be added.');
    values=await saveEducationValue(state,field,[...values,{}],()=>add.click({signal:check.signal}),check);
  }
  for(let index=0;index<answer.length;index++) {
    const target=answer[index];
    values=await selectEducationSchool(page,state,root,field,values,index,target.schoolName,check);
    if(Object.hasOwn(target,'isCurrent'))values=await editEducationPart(page,state,root,field,values,index,'isCurrent',target.isCurrent,check);
    for(const part of ['degree','major'])if(Object.hasOwn(target,part))values=await editEducationPart(page,state,root,field,values,index,part,target[part],check);
    for(const part of ['startDate','endDate'])if(target[part])values=await editEducationDate(page,state,root,field,values,index,part,target[part],check);
    await verifyEducationRows(root,values);
  }
  return answer;
}
module.exports={fillAshbyEducation,educationContext,verifyEducationRows,editEducationDate};

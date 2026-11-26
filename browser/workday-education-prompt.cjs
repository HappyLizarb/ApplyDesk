const honorsSuffix=/\s*\(honou?rs\)\s*$/i;

// Search the recorded program, its named components and common catalog spellings.
function studyCandidates(value) {
  const name=value.trim(),clean=name.replace(honorsSuffix,'').trim();
  const full=[...new Set([name,clean].filter(Boolean))];
  const components=clean.split(/\s*(?:&|\band\b)\s*/i).filter(Boolean);
  const base=[...new Set([...full,...components])];
  const alternatives=[];
  for(const candidate of base) {
    if(/\sengineering science$/i.test(candidate))alternatives.push(candidate.slice(0,-8));
    if(/\ssciences$/i.test(candidate))alternatives.push(candidate.slice(0,-1));
  }
  const queries=[...new Set([...base,...alternatives])];
  // A truncated list could conceal a second matching component.
  return queries.length<=6?queries:full;
}

// Typography may vary; a partial or merely similar label is not an exact option match.
function studyKey(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
}

// Workday's official prompt client receives an array of canonical id/descriptor pairs.
function uniqueStudyChoice(data,query) {
  if(!Array.isArray(data)||data.some(item=>!item||typeof item.id!=='string'||typeof item.descriptor!=='string'))
    throw new Error('The field-of-study lookup response changed; capture its schema before continuing.');
  const matches=data.filter(item=>!item.href&&studyKey(item.descriptor)===studyKey(query));
  if(matches.length>1)throw new Error('The field-of-study lookup returned multiple exact choices.');
  return matches[0]||null;
}

// Wait for the actual input-triggered search response so stale menus cannot satisfy a new query.
async function searchStudy(page,input,query,check) {
  const pageUrl=page.url();
  const pending=page.waitForResponse(response=>{
    const request=response.request(),url=new URL(response.url());
    return request.method()==='GET'&&request.frame()===page.mainFrame()
      &&url.origin===new URL(pageUrl).origin&&/\/values\/educations\/fieldsOfStudy$/.test(url.pathname)
      &&url.searchParams.get('search')===query;
  },{timeout:7000}).catch(()=>null);
  check();await input.fill(query,{signal:check.signal});
  const trigger=input.locator('xpath=ancestor::*[@data-automation-id="multiselectInputContainer"]')
    .locator('[data-automation-id="promptSearchButton"]');
  if(await trigger.count()===1&&await trigger.isVisible()) {check();await trigger.click({signal:check.signal});}
  const response=await pending;
  if(!response?.ok())throw new Error('The field-of-study search was not confirmed; inspect its prompt and lookup response.');
  return uniqueStudyChoice(await response.json(),query);
}

// The public widget binds each leaf to its input's multiselect ID and canonical result ID.
async function chooseStudy(page,input,choice,check) {
  const binding=await input.getAttribute('data-uxi-multiselect-id');
  if(!binding)throw new Error('The field-of-study prompt has no bound result list.');
  const selector=`[data-automation-id="promptLeafNode"][data-uxi-widget-type="multiselectlistitem"]`
    +`[data-uxi-multiselect-id=${JSON.stringify(binding)}][data-uxi-multiselectlistitem-instanceid=${JSON.stringify(choice.id)}]`;
  const option=page.locator(selector);
  await option.waitFor({state:'visible',timeout:5000});
  if(await option.count()!==1)throw new Error('The field-of-study result is ambiguous.');
  const label=await option.locator('[data-automation-id="promptOption"]').getAttribute('data-automation-label');
  if(label!==choice.descriptor)throw new Error('The field-of-study result label changed.');
  check();await option.click({signal:check.signal});
}

// Prefer the complete program and require all fallback matches to identify the same option.
async function resolveStudy(page,input,source,check) {
  const complete=studyKey(source.replace(honorsSuffix,''));
  let match,matchedQuery,current;
  for(const query of studyCandidates(source)) {
    current=await searchStudy(page,input,query,check);
    if(!current)continue;
    if(studyKey(query.replace(honorsSuffix,''))===complete)return current;
    if(match&&(match.id!==current.id||match.descriptor!==current.descriptor))
      throw new Error('Multiple field-of-study choices match the saved program.');
    match=current;matchedQuery=query;
  }
  if(!match)throw new Error('No unique field-of-study choice matches the saved program.');
  if(current)return match;
  // Subsequent searches replaced the menu. Verify the same option before selecting it.
  const fresh=await searchStudy(page,input,matchedQuery,check);
  if(fresh?.id!==match.id||fresh.descriptor!==match.descriptor)
    throw new Error('The matched field-of-study choice changed during lookup.');
  return fresh;
}

// Only a unique observed choice can replace a profile's exact program name with a category.
async function fillStudy(page,state,row,index,field,source,check) {
  const generation=state.generation||0;
  const container=row.locator('[data-automation-id="formField-fieldOfStudy"]');
  const input=container.locator('input[data-uxi-widget-type="selectinput"]');
  if(await input.count()!==1)throw new Error('The education field-of-study control changed.');
  const choice=await resolveStudy(page,input,source,check);
  await chooseStudy(page,input,choice,check);
  const selected=container.locator('[data-automation-id="selectedItem"] [data-automation-id="promptOption"]');
  if(await selected.count()!==1||(await selected.innerText()).trim()!==choice.descriptor)
    throw new Error('The selected field of study could not be read back.');
  check();
  state.workdayEducationChoices ||= new Map();
  state.workdayEducationChoices.set(`${field.field_id}:${index}`,{pageUrl:page.url(),generation,...choice});
  return choice.descriptor;
}
module.exports={studyCandidates,studyKey,uniqueStudyChoice,searchStudy,chooseStudy,fillStudy};

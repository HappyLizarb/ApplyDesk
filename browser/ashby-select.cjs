const {waitForAshbySave}=require('./ashby-drafts.cjs');

// Only the employer's exact single-choice definition can turn an autocomplete into a select.
function annotateAshbySelects(snapshot,state) {
  for(const field of snapshot.fields.filter(field=>field.type==='combobox')) {
    const binding=field.ashby_binding,form=state.ashbyForms?.get(binding?.form),definition=form?.fields.get(binding?.path);
    if(form?.pageUrl!==snapshot.url||definition?.type!=='ValueSelect'||definition.isMany
        ||definition.title?.trim()!==field.label.trim()||!Array.isArray(definition.selectableValues))continue;
    const options=definition.selectableValues.map(option=>({...option,disabled:false}));
    const selected=options.filter(option=>option.label===field.value);
    if(!options.length||options.some(option=>typeof option.value!=='string'||!option.label)
        ||new Set(options.map(option=>option.value)).size!==options.length||field.value&&selected.length!==1)continue;
    Object.assign(field,{type:'select',widget:'ashby_select',supported:true,options,value:selected[0]?.value||''});
  }
}

// Use the bound visible menu and await the employer's acknowledgement of the exact option value.
async function selectAshbyOption(page,element,field,value,edit,check) {
  const choices=field.options.filter(option=>option.value===value&&!option.disabled);
  if(choices.length!==1)throw new Error('Choose one observed Ashby option.');
  const label=choices[0].label;
  if(await element.inputValue()!==label) {
    check();await element.fill(label,{signal:check.signal});
    await page.waitForFunction(input=>input.getAttribute('aria-expanded')==='true'&&input.getAttribute('aria-controls'),element,{timeout:5000});
    const id=await element.getAttribute('aria-controls'),menu=page.locator(`[id=${JSON.stringify(id)}][role="listbox"]`);
    const option=menu.getByRole('option',{name:label,exact:true});
    await option.waitFor({state:'visible',timeout:5000});
    if(await option.count()!==1)throw new Error('The Ashby dropdown option is ambiguous.');
    check();await option.click({signal:check.signal});
  }
  await waitForAshbySave(edit,check);
  if(await element.inputValue()!==label)throw new Error('The Ashby dropdown did not retain the selected answer.');
  return value;
}
module.exports={annotateAshbySelects,selectAshbyOption};

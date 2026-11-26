const {checkpoint}=require('./control.cjs');

// Treat the phone picker as one selected value, not its search box and display list.
function workdayProxy(element) {
  return /^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname)
    && !!element.closest('[data-automation-id="formField-countryPhoneCode"]');
}

// Native popup buttons and the selected phone code retain their real DOM identities.
function workdayControls(root, identity, clean) {
  if (!/^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname)) return [];
  const controls=[...root.querySelectorAll('[data-automation-id^="formField-"] button[aria-haspopup="listbox"]')];
  const phone=root.querySelector('[data-automation-id="formField-countryPhoneCode"]');
  if (phone) controls.push(phone);
  return controls.filter(element=>element.getClientRects().length && !element.closest('[role="group"][aria-labelledby="Education-section"]')).map(element=>{
    const selected=element===phone, container=selected?phone:element.closest('[data-automation-id^="formField-"]');
    const label=clean(container.querySelector('label')?.innerText), context=element.closest('form,main,[role="main"]');
    const pills=selected?[...element.querySelectorAll('[data-automation-id="promptOption"]')]:[];
    const code=pills.length===1?clean(pills[0].innerText).match(/\((\+\d{1,4})\)$/)?.[1]:null;
    return {field_id:identity(element),label:selected?'Phone country code':label,name:element.name||'',
      type:selected?'text':'select',widget:selected?'workday_phone_code':'workday_select',
      value:selected?(code||''):(element.value?clean(element.innerText):''),readonly:selected,
      required:!!container.querySelector('label abbr'),disabled:!!element.disabled,multiple:false,
      supported:selected?!!code:true,options:selected?undefined:[],choices_unread:!selected,
      form_id:element.form?identity(element.form):null,context_id:context?identity(context):null};
  });
}

// Cache only options read from this exact document's dropdown; old page IDs cannot reuse them.
function annotateWorkdayControls(snapshot,state) {
  for (const field of snapshot.fields.filter(field=>field.widget==='workday_select')) {
    const options=state.workdayChoices?.get(field.field_id);
    if (options) {field.options=options;field.choices_unread=false;}
  }
}

// Workday binds each open popup to its button after rendering; ignore other live menus.
async function workdayMenu(page,element,check) {
  check();await element.click({signal:check.signal});
  await page.waitForFunction(button=>{
    const menu=document.getElementById(button.getAttribute('aria-controls'));
    const choices=[...menu?.querySelectorAll('[role="option"]')||[]];
    const available=choices.filter(option=>option.getAttribute('aria-disabled')!=='true'
      && option.getAttribute('data-value')!=='' && option.innerText.trim());
    return button.getAttribute('aria-expanded')==='true' && menu?.getAttribute('role')==='listbox'
      && menu.getClientRects().length && available.length>0
      && (!button.value || choices.some(option=>option.innerText.trim()===button.innerText.trim()));
  },element,{timeout:5000});
  const id=await element.getAttribute('aria-controls');
  return page.locator(`[id=${JSON.stringify(id)}][role="listbox"]`);
}

// Opening a verified field menu reads its choices without choosing an answer or posting a form.
async function readWorkdayChoices(page,state,params) {
  const {inspect}=require('./inspect.cjs'),check=checkpoint(state),snapshot=await inspect(page,state);
  const field=snapshot.fields.find(field=>field.field_id===params.field_id);
  if (snapshot.snapshot_id!==params.snapshot_id || field?.widget!=='workday_select') throw new Error('The dropdown changed before its choices were read.');
  const handle=await page.evaluateHandle(id=>globalThis.__applyDesk.elements.get(id),field.field_id);
  const element=handle.asElement(),menu=await workdayMenu(page,element,check);
  let options;
  try {
    options=await menu.locator('[role="option"]').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>({
      label:n.innerText.trim(),value:n.innerText.trim(),disabled:n.getAttribute('aria-disabled')==='true'})));
  } finally {await page.keyboard.press('Escape');await menu.waitFor({state:'hidden',timeout:5000});}
  check();
  if (!options?.length || options.some(o=>!o.value) || new Set(options.map(o=>o.value)).size!==options.length) throw new Error('The dropdown choices are missing or ambiguous.');
  state.workdayChoices ||= new Map();state.workdayChoices.set(field.field_id,options);
  return {...await inspect(page,state),intervention:'dropdown_choices_read'};
}

// Choose an observed label and verify the native button's selected text independently.
async function setWorkdayControl(page,element,field,value,check) {
  if (field.widget==='workday_phone_code') {
    const labels=await element.evaluate(root=>[...root.querySelectorAll('[data-automation-id="promptOption"]')].map(n=>n.innerText));
    if (labels.length!==1 || labels[0].trim().match(/\((\+\d{1,4})\)$/)?.[1]!==value) throw new Error('The selected phone code differs from the confirmed profile.');
    return value;
  }
  if (!field.options.some(option=>option.value===value&&!option.disabled)) throw new Error('Choose one observed dropdown option.');
  if (await element.innerText()!==value) {
    const menu=await workdayMenu(page,element,check);
    const option=menu.getByRole('option',{name:value,exact:true});
    await option.waitFor({state:'visible',timeout:5000});
    if (await option.count()!==1) throw new Error('The dropdown choice is ambiguous.');
    check();await option.click({signal:check.signal});
  }
  if ((await element.innerText()).trim()!==value) throw new Error('The dropdown selection could not be verified.');
  return value;
}
module.exports={workdayProxy,workdayControls,annotateWorkdayControls,readWorkdayChoices,setWorkdayControl};

const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {annotateAshbySelects,selectAshbyOption}=require('../browser/ashby-select.cjs');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const page=await browser.newPage(),edit={saved:false,expires:Date.now()+30000};
  await page.exposeFunction('saved',()=>{edit.saved=true});
  await page.setContent(`<input role="combobox" aria-expanded="false" oninput="this.setAttribute('aria-expanded','true');this.setAttribute('aria-controls','choices');document.querySelector('#choices').hidden=false"><div id="choices" role="listbox" hidden><div role="option" onclick="document.querySelector('input').value='LinkedIn';this.parentElement.hidden=true;window.saved()">LinkedIn</div></div>`);
  const field={field_id:'source',type:'combobox',label:'How did you hear about us?',value:'',ashby_binding:{form:'form',path:'source'}};
  const snapshot={url:page.url(),fields:[field]},state={ashbyForms:new Map([['form',{pageUrl:page.url(),fields:new Map([['source',{type:'ValueSelect',title:field.label,isMany:false,selectableValues:[{label:'LinkedIn',value:'linkedin-id'}]}]])}]])};
  annotateAshbySelects(snapshot,state);assert.equal(field.widget,'ashby_select');assert.equal(field.options[0].value,'linkedin-id');
  const input=await page.locator('input').elementHandle();
  assert.equal(await selectAshbyOption(page,input,field,'linkedin-id',edit,()=>{}),'linkedin-id');
  assert.equal(await input.inputValue(),'LinkedIn');
  await assert.rejects(selectAshbyOption(page,input,field,'guess',edit,()=>{}),/observed/);
  field.type='combobox';field.value='Unconfirmed';annotateAshbySelects(snapshot,state);assert.equal(field.type,'combobox');
  console.log('Ashby choices bind the employer definition and select the exact visible answer with saved-value readback.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

const {isDeepStrictEqual}=require('node:util');

// Remember only already-approved definition reads for this exact page and application.
function rememberWorkdayDefinition(request,state) {
  const url=new URL(request.url()),match=url.pathname.match(/\/jobapplication\/([a-z0-9-]+)\/jobapplication\/([a-f0-9]{32})\/([a-z]+)$/);
  if (!match) return;
  const pageUrl=request.frame().url();
  if (state.workdayDefinition?.pageUrl!==pageUrl || state.workdayDefinition?.id!==match[2])
    state.workdayDefinition={pageUrl,id:match[2],tenant:match[1],values:{}};
  state.workdayDefinition.values[match[3]]=request.postDataJSON();
}

// Serialize the inspected first-page controls, preserving Workday's own option identifiers.
function personalDraftValues(fields) {
  const elements=globalThis.__applyDesk.elements;
  const byName=name=>fields.find(field=>field.name===name);
  const text=name=>byName(name)?.value||'';
  const choice=name=>{const field=byName(name),element=elements.get(field?.field_id);return element?.value?{id:element.value,descriptor:field.value}:null;};
  const country=choice('country'),region=choice('countryRegion'),phoneType=choice('phoneType');
  const previous=fields.find(field=>field.name==='candidateIsPreviousWorker'&&field.checked);
  if (!country || !phoneType || !previous || !['true','false'].includes(previous.value) || byName('preferredCheck')?.checked)
    throw new Error('The Workday personal-information draft requires reviewed controls.');
  const address={};
  for(const name of ['addressLine1','city','postalCode']) if(text(name)) address[name]=text(name);
  if(region) address.countryRegion=region;
  address.country={id:country.id};
  const phone={phoneType,phoneNumber:text('phoneNumber')};
  if(text('extension')) phone.extension=text('extension');
  return {name:{legalName:{firstName:text('legalName--firstName'),lastName:text('legalName--lastName'),country:{id:country.id}},preferredCheck:false},
    address,phonenumber:phone,previousworker:{candidateIsPreviousWorker:previous.value==='true'}};
}

// A verified Save and Continue permits only the audited first-page payloads, never submission.
async function armWorkdayDraft(page,state,snapshot,button,audit) {
  const definition=state.workdayDefinition;
  if (!definition || definition.pageUrl!==snapshot.url || button.text!=='Save and Continue') return false;
  if (!snapshot.fields.some(field=>field.name==='legalName--firstName')) return false;
  if (!audit?.passed || audit.snapshot_id!==snapshot.snapshot_id) throw new Error('Review Workday fields before saving the draft.');
  const covered=new Set(audit.field_ids),unknown=snapshot.fields.some(field=>{
    const value=['radio','checkbox'].includes(field.type)?field.checked:field.value;
    return value && !covered.has(field.field_id);
  });
  if(unknown) throw new Error('An unaudited Workday value cannot be saved.');
  const values=await page.evaluate(personalDraftValues,snapshot.fields);
  const code=definition.values.phonenumber?.countryPhoneCode;
  const selected=snapshot.fields.find(field=>field.widget==='workday_phone_code')?.value;
  if(!code?.id || !selected || !code.descriptor?.endsWith(`(${selected})`)) throw new Error('The Workday phone-code definition changed.');
  values.phonenumber.countryPhoneCode=code;
  if(definition.values.source) values.source=definition.values.source;
  state.workdayDraft={pageUrl:snapshot.url,id:definition.id,tenant:definition.tenant,values,generation:state.generation||0,expires:Date.now()+30000,used:new Set()};
  return true;
}

// Endpoint, frame, application, method and exact reviewed payload must all match.
function workdayDraftRequest(request,state) {
  try {
    const permit=state.workdayDraft,frame=request.frame(),url=new URL(request.url());
    if(!permit || permit.generation!==(state.generation||0) || Date.now()>permit.expires || frame.url()!==permit.pageUrl || frame!==frame.page().mainFrame()
      || url.origin!==new URL(permit.pageUrl).origin || url.search || request.headers()['form-definition']) return false;
    if(url.pathname===`/wday/cxs/${permit.tenant}/jobapplication/${permit.id}/initialize`) {
      if(request.method()!=='POST'||request.postData()||permit.used.has('initialize')) return false;
      permit.used.add('initialize');return true;
    }
    const base=`/wday/calypso/cxs/jobapplication/${permit.tenant}/`;
    if(url.pathname===`${base}package/${permit.id}/validate`) return request.method()==='PUT'&&!request.postData();
    const prefix=`${base}jobapplication/${permit.id}/`;
    if(!url.pathname.startsWith(prefix) || request.method()!=='POST') return false;
    const endpoint=url.pathname.slice(prefix.length);
    if(!Object.hasOwn(permit.values,endpoint) || permit.used.has(endpoint)
      || !isDeepStrictEqual(request.postDataJSON(),permit.values[endpoint])) return false;
    permit.used.add(endpoint);return true;
  } catch {return false;}
}
module.exports={rememberWorkdayDefinition,personalDraftValues,armWorkdayDraft,workdayDraftRequest};

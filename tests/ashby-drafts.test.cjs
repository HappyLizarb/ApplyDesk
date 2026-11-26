const assert = require('node:assert/strict');
const {armAshbyAnswer, ashbyDraftRequest} = require('../browser/ashby-drafts.cjs');
const queries = require('./fixtures/ashby-draft-queries.json');
const pageUrl='https://jobs.ashbyhq.com/example/job/application';
const definition={type:'String'};
const state={ashbyForms:new Map([['form',{definition:'definition',pageUrl,fields:new Map([['name',definition]])}]]),ashbyEdits:new Map()};
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};
const values={organizationHostedJobsPageName:'example',formRenderIdentifier:'form',path:'name',value:'Test Applicant',formDefinitionIdentifier:'definition'};
const request=(variables,extra={})=>({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql',frame:()=>frame,method:()=> 'POST',
  postDataJSON:()=>({operationName:'ApiSetFormValue',query:queries.ApiSetFormValue,variables,...extra})});
assert.equal(ashbyDraftRequest(request(values),state),false);
armAshbyAnswer(state,{ashby_binding:{form:'form',path:'name'},type:'text'},'Test Applicant');
assert.equal(ashbyDraftRequest(request(values),state),true);
for(const changed of [{value:'Invented Name'},{path:'citizenship'},{formRenderIdentifier:'other'},
  {formDefinitionIdentifier:'other'},{organizationHostedJobsPageName:'other'},{submit:true}]) {
  assert.equal(ashbyDraftRequest(request({...values,...changed}),state),false);
}
assert.equal(ashbyDraftRequest(request(values,{query:queries.ApiSetFormValue+' mutation Submit {}'}),state),false);
state.ashbyEdits.get('form:name').expires=0;
assert.equal(ashbyDraftRequest(request(values),state),false);
console.log('Ashby autosaves require the exact planned value, tenant, form, field, definition and unexpired permit.');

const locationRequest=values=>({...request({}),postDataJSON:()=>({operationName:'ApiAutocompleteGeoLocation',query:queries.ApiAutocompleteGeoLocation,variables:values})});
assert.equal(ashbyDraftRequest(locationRequest({text:'',locationTypes:['Country','Region','City']}),{}),true);
assert.equal(ashbyDraftRequest(locationRequest({text:'Unapproved address',locationTypes:['Country','Region','City']}),{}),false);

// Autosave confirmation must come from the exact permitted request and server value.
(async()=>{
  const {EventEmitter}=require('node:events');
  const {trackAshbyForms,waitForAshbySave}=require('../browser/ashby-drafts.cjs');
  for(const matches of [true,false]) {
    const page=new EventEmitter(),tracked={};page.url=()=>pageUrl;
    trackAshbyForms(page,tracked);
    const sent=request(values),edit={request:sent,form:'form',path:'name',value:'Test Applicant',saved:false};
    tracked.ashbyEdits.set('form:name',edit);
    page.emit('response',{request:()=>sent,ok:()=>true,json:async()=>({data:{setFormValue:{id:'form',sections:[{fieldEntries:[{field:{path:'name'},fieldValue:{value:matches?'Test Applicant':'Different'}}]}]}}})});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(edit.saved,matches);
    if(matches) await waitForAshbySave(edit,()=>{});
    else await assert.rejects(waitForAshbySave(edit,()=>{}),/did not confirm/);
  }
})().catch(error=>{console.error(error);process.exitCode=1});

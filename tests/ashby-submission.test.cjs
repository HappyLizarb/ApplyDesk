const assert = require('node:assert/strict');
const {ashbySubmissionRequest,annotateAshbySubmission} = require('../browser/ashby-submission.cjs');
const queries = require('./fixtures/ashby-draft-queries.json');
const binding={pageUrl:'https://jobs.ashbyhq.com/example/job/application',tenant:'example',job:'job',form:'form',definition:'definition',action:'action',surveys:[{formRenderId:'survey',actionIdentifier:'survey-action',sourceFormDefinitionId:'survey-definition'}]};
const frame={url:()=>binding.pageUrl,page:()=>({mainFrame:()=>frame})};
const variables={organizationHostedJobsPageName:'example',jobPostingId:'job',applicationFormRenderIdentifier:'form',applicationFormDefinitionIdentifier:'definition',applicationFormActionIdentifier:'action',surveyIdentifiers:binding.surveys,recaptchaToken:'synthetic-native-token'};
const request=(values=variables,query=queries.ApiSubmitMultipleFormsAction)=>({method:()=> 'POST',frame:()=>frame,postDataJSON:()=>({operationName:'ApiSubmitMultipleFormsAction',query,variables:values})});
assert.equal(ashbySubmissionRequest(request(),{ashby:binding}),true);
for(const change of [{jobPostingId:'other'},{applicationFormRenderIdentifier:'other'},{applicationFormActionIdentifier:'other'},
  {surveyIdentifiers:[]},{unapproved:true},{recaptchaToken:''}]) assert.equal(ashbySubmissionRequest(request({...variables,...change}),{ashby:binding}),false);
assert.equal(ashbySubmissionRequest(request(variables,queries.ApiSubmitMultipleFormsAction+' mutation Other {anything}'),{ashby:binding}),false);
const state={ashbyApplication:binding,ashbyEdits:new Map([['field',{saved:false}]]),ashbyFiles:[]};
let snapshot={url:binding.pageUrl,warnings:[]};annotateAshbySubmission(snapshot,state);
assert.equal(snapshot.submission_target,undefined);
state.ashbyEdits.get('field').saved=true;
state.ashbyFiles.push({verified:true,binding:{form:'form',path:'_systemfield_resume'}});
snapshot={url:binding.pageUrl,warnings:[]};annotateAshbySubmission(snapshot,state);
assert.equal(snapshot.submission_target,'https://jobs.ashbyhq.com/api/non-user-graphql');
assert.equal(snapshot.submission_provider,'ashby');
console.log('Ashby final submission is bound to the audited employer, posting, saved fields, verified resume and exact forms.');

// An unrelated, rejected or unreadable response must never count as a receipt.
(async()=>{
  const {EventEmitter}=require('node:events');
  const {watchAshbyReceipt}=require('../browser/ashby-submission.cjs');
  for(const [body,ok,expected] of [
    [{data:{submitMultipleFormsAction:{applicationFormResult:{__typename:'FormSubmitSuccess'},surveyFormResults:[{__typename:'FormSubmitSuccess'}]}}},true,true],
    [{data:{submitMultipleFormsAction:{applicationFormResult:{__typename:'FormRender'}}}},true,false],
    [{errors:[{message:'Rejected'}]},true,false],
    [{data:{submitMultipleFormsAction:{applicationFormResult:{__typename:'FormSubmitSuccess'}}}},false,false],
  ]) {
    const page=new EventEmitter(),permit={ashby:binding,request:{}};
    watchAshbyReceipt(page,permit);
    page.emit('response',{request:()=>({}),json:async()=>body,ok:()=>ok});
    await new Promise(resolve=>setImmediate(resolve));assert.equal(permit.receipt,undefined);
    page.emit('response',{request:()=>permit.request,json:async()=>body,ok:()=>ok});
    await new Promise(resolve=>setImmediate(resolve));assert.equal(!!permit.receipt,expected);
    permit.cleanup();assert.equal(page.listenerCount('response'),0);
  }
})().catch(error=>{console.error(error);process.exitCode=1});

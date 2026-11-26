const assert = require('node:assert/strict');
const {ashbyUploadRequest} = require('../browser/ashby-upload.cjs');
const queries = require('./fixtures/ashby-draft-queries.json');
const pageUrl='https://jobs.ashbyhq.com/example/job/application';
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};
const state=()=>({ashbyUploadPlan:{pageUrl,filename:'resume.pdf',size:100,expires:Date.now()+1000}});
const variables={organizationHostedJobsPageName:'example',filename:'resume.pdf',contentLength:100,contentType:'application/pdf',fileUploadContext:'JobApplication'};
const request=(values=variables)=>({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql',frame:()=>frame,method:()=> 'POST',
  postDataJSON:()=>({operationName:'ApiCreateFileUploadHandle',query:queries.ApiCreateFileUploadHandle,variables:values})});
const current=state(), valid=request();
assert.equal(ashbyUploadRequest(valid,current),true);
assert.equal(current.ashbyUploadPlan.request,valid);
assert.equal(ashbyUploadRequest(valid,current),false);
for(const changed of [{filename:'other.pdf'},{contentLength:101},{contentType:'text/plain'},
  {organizationHostedJobsPageName:'other'},{unapproved:'field'}]) {
  assert.equal(ashbyUploadRequest(request({...variables,...changed}),state()),false);
}
const expired=state();expired.ashbyUploadPlan.expires=0;
assert.equal(ashbyUploadRequest(request(),expired),false);
console.log('Ashby signing permits are one-use and bound to the selected PDF metadata and tenant.');
const {ashbyAttachRequest} = require('../browser/ashby-upload.cjs');
const fileState=()=>({ashbyFiles:[{pageUrl,handle:'synthetic-file-handle',binding:{form:'form',path:'resume'},definition:'definition',expires:Date.now()+1000}]});
const attachment={organizationHostedJobsPageName:'example',formRenderIdentifier:'form',path:'resume',formDefinitionIdentifier:'definition',fileHandle:'synthetic-file-handle'};
const attachRequest=values=>({...request(),postDataJSON:()=>({operationName:'ApiSetFormValueToFile',query:queries.ApiSetFormValueToFile,variables:values})});
const attached=fileState();
assert.equal(ashbyAttachRequest(attachRequest(attachment),attached),true);
assert.equal(ashbyAttachRequest(attachRequest(attachment),attached),false);
for(const changed of [{fileHandle:'unverified'},{path:'other'},{formRenderIdentifier:'other'},{formDefinitionIdentifier:'other'}]) {
  assert.equal(ashbyAttachRequest(attachRequest({...attachment,...changed}),fileState()),false);
}
console.log('Ashby attachment requires the exact successfully uploaded handle and destination field.');

// Unrelated storage writes do not wait for a pending application document's policy.
(async()=>{
  const {waitForAshbyUploadPolicy}=require('../browser/ashby-upload.cjs');
  const form=new FormData();form.append('file',new Blob(['other'],{type:'text/plain'}),'unrelated.txt');
  const url='https://fixture.s3.us-east-1.amazonaws.com/';
  const encoded=new Request(url,{method:'POST',body:form}),bytes=Buffer.from(await encoded.arrayBuffer());
  const pending={ashbyUploadPlan:{request:{},policyReady:new Promise(()=>{}),pageUrl,filename:'resume.pdf',expires:Date.now()+30000}};
  const upload={url:()=>url,method:()=> 'POST',frame:()=>frame,postDataBuffer:()=>bytes,
    headers:()=>({'content-type':encoded.headers.get('content-type')})};
  await Promise.race([waitForAshbyUploadPolicy(upload,pending),new Promise((_,reject)=>setTimeout(()=>reject(Error('Unrelated upload waited')),500))]);
  console.log('The pending signing policy does not delay unrelated storage files.');
})().catch(error=>{console.error(error);process.exitCode=1});

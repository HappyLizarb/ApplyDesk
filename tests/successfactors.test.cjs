const assert=require('node:assert/strict');
const {successFactorsEntry}=require('../browser/successfactors.cjs');
const pageUrl='https://jobs.supermicro.com/job/role/1000000001/';
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};
const payload={context:{action:'apply',locale:'en_US',source:'',sourceId:'JATS-supermicro',categoryID:'',jobID:1000000001,brand:'',email:'',signedToken:''},sourceData:'',agent:{keywords:'',location:'',q:'',locationSearch:'',geoLocation:'',longitude:'',latitude:'',distance:'',units:'',country:'',frequency:7,filterString:'',facets:{}}};
const request=data=>({url:()=> 'https://jobs.supermicro.com/services/cas/createpayload/',method:()=> 'POST',frame:()=>frame,postDataJSON:()=>data});
const state=()=>({employerEntry:{pageUrl,posting:'1000000001',expires:Date.now()+1000}});
const once=state();assert.equal(successFactorsEntry(request(payload),once),true);assert.equal(successFactorsEntry(request(payload),once),false);
for(const change of [{action:'submit'},{email:'unapproved@example.test'},{jobID:123},{source:'li'}]){
 assert.equal(successFactorsEntry(request({...payload,context:{...payload.context,...change}}),state()),false);
}
assert.equal(successFactorsEntry(request({...payload,sourceData:'resume'}),state()),false);
const expired=state();expired.employerEntry.expires=0;assert.equal(successFactorsEntry(request(payload),expired),false);
console.log('SuccessFactors entry permits only the observed empty payload for the selected job, once.');
const {handoffFields,successFactorsHandoff}=require('../browser/successfactors.cjs');
const body={success:true,targetUrl:'https://career4.successfactors.com/careers',company:'supermicro',career_job_req_id:'10001',correlation_Id:'synthetic-only'};
const handoff=handoffFields(body);assert.ok(handoff);
assert.equal(handoffFields({...body,targetUrl:'https://unrelated.example/careers'}),null);
for (const requisition of ['10001','10002',10002]) {
  assert.equal(handoffFields({...body,career_job_req_id:requisition}).fields.career_job_req_id,String(requisition));
}
for (const requisition of ['',null,'other',['10001']]) {
  assert.equal(handoffFields({...body,career_job_req_id:requisition}),null);
}
assert.equal(handoffFields({...body,company:'other'}),null);
const navigation=fields=>({url:()=>handoff.url,frame:()=>frame,method:()=> 'POST',isNavigationRequest:()=>true,postData:()=>new URLSearchParams(fields).toString()});
const authorized=()=>({employerHandoff:{...handoff,pageUrl,expires:Date.now()+1000,used:false}});
assert.equal(successFactorsHandoff(navigation({...handoff.fields,extra:'value'}),authorized()),false);
assert.equal(successFactorsHandoff(navigation({...handoff.fields,company:'other'}),authorized()),false);
assert.equal(successFactorsHandoff(navigation({...handoff.fields,career_job_req_id:'10002'}),authorized()),false);
const one=authorized();assert.equal(successFactorsHandoff(navigation(handoff.fields),one),true);assert.equal(successFactorsHandoff(navigation(handoff.fields),one),false);

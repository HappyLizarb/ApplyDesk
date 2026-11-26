const assert = require('node:assert/strict');
const {draftInitialization} = require('../browser/workday.cjs');
const origin='https://example.wd5.myworkdayjobs.com';
const frame={url:()=>origin+'/en-US/Example/job/US/Test_JR123/apply/applyManually',page:()=>({mainFrame:()=>frame})};
const request={url:()=>origin+'/wday/cxs/example/jobpostings/Test_JR123/jobapplications',frame:()=>frame,method:()=>'POST',postDataJSON:()=>({})};
assert.equal(draftInitialization(request),true);
for(const change of [
  {postDataJSON:()=>({submit:true})}, {postDataJSON:()=>[]}, {postDataJSON:()=>null},
  {url:()=>origin+'/wday/cxs/other/jobpostings/Test_JR123/jobapplications'},
  {url:()=>origin+'/wday/cxs/example/jobpostings/Test_JR124/jobapplications'},
  {url:()=>origin+'/wday/cxs/example/jobpostings/Test_JR123/jobapplications?submit=true'},
  {method:()=>'PUT'}, {frame:()=>({...frame})}
]) assert.equal(draftInitialization({...request,...change}),false);
console.log('Empty Workday draft initialization is limited to the exact employer, posting, frame and payload.');

const {workdayFormDefinition}=require('../browser/workday.cjs');
const definition={...request,url:()=>origin+'/wday/calypso/cxs/jobapplication/example/jobapplication/'+ 'a'.repeat(32)+'/emailaddress',
  headers:()=>({'form-definition':'true'}),postDataJSON:()=>({emailAddress:'synthetic@example.test'})};
assert.equal(workdayFormDefinition(definition),true);
for (const changes of [{headers:()=>({})},{headers:()=>({'form-definition':'false'})},
 {postDataJSON:()=>({submit:true})},{method:()=> 'PUT'},{url:()=>definition.url().replace('/emailaddress','/submit')},
 {url:()=>definition.url().replace('/example/','/other/')},{url:()=>definition.url()+'?submit=true'}]) {
 assert.equal(workdayFormDefinition({...definition,...changes}),false);
}
console.log('Workday definition reads remain distinct from account, draft and final writes.');

const previous={...definition,url:()=>definition.url().replace('/emailaddress','/previousworker')};
for(const value of [true,false]) assert.equal(workdayFormDefinition({...previous,postDataJSON:()=>({candidateIsPreviousWorker:value})}),true);
for(const value of ['false',null,0,{}]) assert.equal(workdayFormDefinition({...previous,postDataJSON:()=>({candidateIsPreviousWorker:value})}),false);
assert.equal(workdayFormDefinition({...previous,headers:()=>({}),postDataJSON:()=>({candidateIsPreviousWorker:false})}),false);
console.log('Conditional previous-employment schema reads accept only boolean answers and never authorize saves.');

for(const [endpoint,body] of [['educations',{}],['workexperiences',{currentlyWorkHere:false}],['skills',{skills:null}],['socials',{facebookAccount:'',linkedInAccount:'',twitterAccount:''}],['resumeattachments',{}]]) {
 const read={...definition,url:()=>definition.url().replace('/emailaddress','/'+endpoint),postDataJSON:()=>body};
 assert.equal(workdayFormDefinition(read),true);
 assert.equal(workdayFormDefinition({...read,headers:()=>({})}),false);
 assert.equal(workdayFormDefinition({...read,postDataJSON:()=>({...body,submit:true})}),false);
}

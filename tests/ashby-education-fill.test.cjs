const {ashbyContext}=require('../browser/ashby-fields.cjs');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {chromium}=require('playwright');
const {trackAshbyForms,ashbyDraftRequest}=require('../browser/ashby-drafts.cjs');
const {educationRequest}=require('../browser/ashby-education-network.cjs');
const {fillAshbyEducation}=require('../browser/ashby-education-fill.cjs');
const {ashbyEducationFields,ashbyEducationRows,ashbyEducationDate}=require('../browser/ashby-education.cjs');
const {ApiSetFormValue}=require('./fixtures/ashby-draft-queries.json');
const path='_systemfield_education_history',url='https://jobs.ashbyhq.com/example/job/application';

// The server fixture acknowledges only writes allowed by the production request policies.
async function routeFixture(route,state,html,blocked,saves) {
 const request=route.request();
 if(request.method()!=='POST')return route.fulfill({contentType:'text/html',body:html});
 const body=request.postDataJSON();
 if(!educationRequest(request,state)&&!ashbyDraftRequest(request,state)) {blocked.push(body);return route.abort();}
 if(body.operationName==='ApiSearchSchoolByCanonicalName') {
  state.schoolRequestCount=(state.schoolRequestCount||0)+1;
  return route.fulfill({json:{data:{result:[{id:'school-1',name:'Example University',country:'US',domain:'example.edu'}]}}});
 }
 saves.push(body.variables.value);
 return route.fulfill({json:{data:{setFormValue:{id:'form',sections:[{fieldEntries:[{field:{path},fieldValue:{value:body.variables.value}}]}]}}}});
}
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const page=await browser.newPage(),state={},blocked=[],saves=[];
  trackAshbyForms(page,state);
  const html=await fs.readFile(__dirname+'/fixtures/ashby-education.html','utf8');
  const schoolQuery=await fs.readFile(__dirname+'/fixtures/ashby-school-query.graphql','utf8');
  await page.route('**/*',route=>routeFixture(route,state,html,blocked,saves));
  await page.goto(url);await page.evaluate(({saveQuery,schoolQuery})=>Object.assign(window,{saveQuery,schoolQuery}),{saveQuery:ApiSetFormValue,schoolQuery});
  state.ashbyForms.set('form',{definition:'definition',pageUrl:url,fields:new Map([[path,{type:'EducationHistory',isRepeatable:true,maxRepeat:3}]]),values:new Map([[path,[]]])});
  const field={widget:'ashby_education',ashby_binding:{form:'form',path}};
  const answer=['Linguistics','Geography'].map(major=>({schoolName:'Example University',degree:'Bachelor of Arts',major,startDate:'2013-08',isCurrent:true}));
  assert.deepEqual(await fillAshbyEducation(page,state,field,answer,()=>{}),answer);
  const actual=await page.evaluate(`(()=>{const ashbyContext=${ashbyContext};const ashbyEducationDate=${ashbyEducationDate};const ashbyEducationRows=${ashbyEducationRows};return (${ashbyEducationFields})(document,()=>'',value=>value?.trim()||'')[0].value})()`);
  assert.deepEqual(actual,answer);
  assert.equal(state.schoolRequestCount,1);
  const oldBody={operationName:'ApiSetFormValue',query:ApiSetFormValue,variables:{organizationHostedJobsPageName:'example',formRenderIdentifier:'form',formDefinitionIdentifier:'definition',path,value:saves[1]}};
  const frame={url:()=>url,page:()=>({mainFrame:()=>frame})};
  const stale={url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql',frame:()=>frame,method:()=> 'POST',postDataJSON:()=>oldBody};
  assert.equal(educationRequest(stale,state)||ashbyDraftRequest(stale,state),false);
  assert.equal(blocked.length,0);assert(saves.length>5);
  const final=saves.at(-1);assert.equal(final.length,2);
  assert.deepEqual(final.map(row=>row.major),answer.map(row=>row.major));
  for(const row of final){assert.equal(row.isCurrent,true);assert.equal(row.startDate,'2013-08-01');assert.equal(row.canonicalSchoolId,'school-1');assert(!Object.hasOwn(row,'endDate'));}
  console.log('Education fills two visible degrees from one verified cache-first school lookup, confirms each save, and keeps both degrees in progress.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

const assert=require('node:assert/strict');
const {personalDraftValues,armWorkdayDraft,workdayDraftRequest,rememberWorkdayDefinition}=require('../browser/workday-drafts.cjs');
const pageUrl='https://example.wd5.myworkdayjobs.com/en-US/Site/job/US/Test_JR1/apply/applyManually';
const origin=new URL(pageUrl).origin,id='a'.repeat(32),prefix=`/wday/calypso/cxs/jobapplication/example/`;
const fields=[['country','United States of America'],['countryRegion','California'],['phoneType','Home Cellular'],
 ['legalName--firstName','Example'],['legalName--lastName','Person'],['addressLine1','Example street'],['city','Example city'],['phoneNumber','5550000']]
 .map(([name,value],i)=>({name,value,field_id:String(i),type:'text'}));
fields.push({name:'candidateIsPreviousWorker',type:'radio',checked:true,value:'false',field_id:'previous'},
 {widget:'workday_phone_code',type:'text',value:'+1',field_id:'code'});
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};
const request=(endpoint,body,method='POST')=>({url:()=>origin+prefix+`jobapplication/${id}/${endpoint}`,frame:()=>frame,method:()=>method,headers:()=>({}),postDataJSON:()=>body,postData:()=>JSON.stringify(body)});
(async()=>{
 globalThis.__applyDesk={elements:new Map(fields.map(f=>[f.field_id,{value:'id-'+f.name}]))};
 const values=personalDraftValues(fields);
 assert.equal(values.previousworker.candidateIsPreviousWorker,false);
 assert.deepEqual(values.name.legalName,{firstName:'Example',lastName:'Person',country:{id:'id-country'}});
 const state={},definition=request('phonenumber',{countryPhoneCode:{id:'phone-code',descriptor:'United States of America (+1)'}});
 rememberWorkdayDefinition(definition,state);
 const snapshot={url:pageUrl,snapshot_id:'snapshot',fields},button={text:'Save and Continue'};
 const page={evaluate:async(fn,args)=>fn(args)},audit={passed:true,snapshot_id:'snapshot',field_ids:fields.map(f=>f.field_id)};
 await assert.rejects(armWorkdayDraft(page,state,snapshot,button,{}),/Review/);
 await assert.rejects(armWorkdayDraft(page,state,snapshot,button,{...audit,field_ids:[]}),/unaudited/);
 assert.equal(await armWorkdayDraft(page,state,snapshot,button,audit),true);
 assert.equal(workdayDraftRequest(request('name',{...values.name,unexpected:true}),state),false);
 assert.equal(workdayDraftRequest(request('name',values.name),state),true);
 assert.equal(workdayDraftRequest(request('name',values.name),state),false);
 assert.equal(workdayDraftRequest(request('submit',values.name),state),false);
 const validate={...request('validate',null,'PUT'),url:()=>origin+prefix+`package/${id}/validate`,postData:()=>null};
 assert.equal(workdayDraftRequest(validate,state),true);
 assert.equal(workdayDraftRequest({...validate,method:()=>'POST'},state),false);
 assert.equal(workdayDraftRequest({...validate,url:()=>origin+prefix+`package/${'b'.repeat(32)}/validate`},state),false);
 const initialize={...request('initialize',null),url:()=>origin+`/wday/cxs/example/jobapplication/${id}/initialize`,postData:()=>null};
 assert.equal(workdayDraftRequest({...initialize,postData:()=>'{"submit":true}'},state),false);
 assert.equal(workdayDraftRequest(initialize,state),true);
 assert.equal(workdayDraftRequest(initialize,state),false);
 const {revoke}=require('../browser/control.cjs');
 assert.equal(workdayDraftRequest(request('previousworker',values.previousworker),state),true);
 await armWorkdayDraft(page,state,snapshot,button,audit);
 revoke(state);state.paused=false;
 assert.equal(workdayDraftRequest(request('previousworker',values.previousworker),state),false);
 state.workdayDraft.expires=0;assert.equal(workdayDraftRequest(validate,state),false);
 console.log('Workday saves require audited values, exact application/frame/method/payload, one use and expiry; final submission stays blocked.');
})().catch(error=>{console.error(error);process.exitCode=1});

const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {start,dispatch}=require('../browser/worker.cjs');
const {successFactorsRead}=require('../browser/successfactors-reads.cjs');
const binding={tenant:'supermicro',requisition:'10001',source_url:'https://jobs.supermicro.com/job/test/1000000001/',company:'Supermicro',title:'Fixture Systems Engineer'};
const frame={url:()=> 'https://career4.successfactors.com/careers',page:()=>({mainFrame:()=>frame})};
const body='callCount=1\npage=test\nhttpSessionId=\nscriptSessionId=fixture\nc0-scriptName=pwdPolicyControllerProxy\nc0-methodName=getPasswordPolicy\nc0-id=0\nc0-param0=string:false\nbatchId=0\n';
const req=value=>({frame:()=>frame,url:()=> 'https://career4.successfactors.com/xi/ajax/remoting/call/plaincall/pwdPolicyControllerProxy.getPasswordPolicy.dwr',method:()=> 'POST',postData:()=>value});
assert.equal(successFactorsRead(req(body),{successFactorsBinding:binding}),true);
assert.equal(successFactorsRead(req(body.replace('getPasswordPolicy','createAccount')),{successFactorsBinding:binding}),false);
assert.equal(successFactorsRead(req(body+'c1-methodName=submit\n'),{successFactorsBinding:binding}),false);
assert.equal(successFactorsRead(req(body),{}),false);
(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-sf-auth-'));
 const s=await start({dataRoot:root,headless:true,allowLocal:true});
 try{
  await s.context.route('**/*',route=>route.fulfill({contentType:'text/html',body:`<h1>Career Opportunities: Sign In</h1><form id="careerform" method="post" action="/career?_s.crb=fixture"><input type="hidden" name="career_company" value="supermicro"><input type="hidden" name="career_job_req_id" value="10001"><input name="username"><input type="password" name="password"><button type="submit">Sign In</button></form><a href="/career?company=supermicro&career_job_req_id=10001&career_ns=job_application&login_ns=register">Create an account</a>`}));
  s.state.successFactorsBinding=binding;
  const view=await dispatch(s,'open',{url:'https://career4.successfactors.com/careers'});
  assert.equal(view.authentication_entry.company,'Supermicro');
  assert.equal(view.fields.find(f=>f.name==='username').label,'Email address');
  assert.equal(view.buttons.find(b=>b.text==='Create Account').action,'advance');
  await s.page.$eval('[name="career_job_req_id"]',e=>e.value='10002');
  const changed=await dispatch(s,'snapshot',{});assert.equal(changed.authentication_entry,undefined);
  s.state.successFactorsBinding={...binding,requisition:'10002'};
  const rebound=await dispatch(s,'snapshot',{});
  assert.equal(rebound.authentication_entry.requisition,'10002');
  assert.equal(rebound.buttons.some(button=>button.navigation_kind==='account_entry'),false);
  s.state.successFactorsBinding=binding;
  await s.page.setContent(`<form id="careerform" method="post" action="/career?_s.crb=fixture"><input type="hidden" name="career_company" value="supermicro"><input type="hidden" name="career_job_req_id" value="10001"><input name="fbclc_userName"><input name="fbclc_pwd" type="password"><input name="fbclc_pwdConf" type="password"><select name="fbclc_country"><option value="US">United States</option></select><a id="dataPrivacyId" role="button" tabindex="0" onclick="document.querySelector('#opened').textContent='Policy opened'">Read and accept the data privacy statement.</a><button id="fbclc_createAccountButton" type="submit">Create Account</button><p id="opened"></p></form>`);
  const registration=await dispatch(s,'snapshot',{});
  const privacy=registration.buttons.filter(b=>b.navigation_kind==='account_privacy');
  assert.equal(privacy.length,1);
  assert.equal(registration.buttons.filter(b=>b.field_id===privacy[0].field_id).length,1);
  const opened=await dispatch(s,'next',{snapshot_id:registration.snapshot_id,field_id:privacy[0].field_id});
  assert.match(opened.text,/Policy opened/);
  await s.page.evaluate(()=>{
    const form=document.querySelector('#careerform'),button=document.querySelector('#fbclc_createAccountButton');
    button.name='fbclc_createAccountButton';button.setAttribute('onclick',"setField('userMsgReq','false');return submitCreateAccount();");
    for(const [name,value] of [['userMsgReq','true'],['fbclc_dpcsId','reviewed-fixture']]) {
      const field=document.createElement('input');field.type='hidden';field.name=name;field.value=value;form.append(field);
    }
  });
  const ready=await dispatch(s,'snapshot',{}),create=ready.buttons.find(b=>b.text==='Create Account');
  const permit=await require('../browser/successfactors-auth.cjs').successFactorsAuthPermit(s.page,ready,create,Date.now()+1000);
  assert.equal(permit.fields.find(([name])=>name==='userMsgReq')[1],'false');
  assert(permit.fields.findIndex(([name])=>name==='fbclc_createAccountButton')<permit.fields.findIndex(([name])=>name==='fbclc_dpcsId'));
  console.log('Verified employer handoffs identify account screens; different requisitions and batched writes remain rejected.');
 }finally{await dispatch(s,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

const {successFactorsAuthRequest}=require('../browser/successfactors-auth.cjs');
const accountPermit=()=>({successfactors:true,used:false,expires:Date.now()+1000,pageUrl:frame.url(),url:'https://career4.successfactors.com/career?_s.crb=fixture',fields:[['company','supermicro'],['career_ns','job_application'],['career_ns','job_application'],['username','synthetic@example.test'],['password','synthetic-not-a-real-password']]});
const accountRequest=values=>({frame:()=>frame,url:()=>accountPermit().url,method:()=> 'POST',postData:()=>new URLSearchParams(values).toString()});
const auth=accountPermit();assert.equal(successFactorsAuthRequest(accountRequest(auth.fields),auth),true);assert.equal(successFactorsAuthRequest(accountRequest(auth.fields),auth),false);
assert.equal(successFactorsAuthRequest(accountRequest([...accountPermit().fields,['extra','submit']]),accountPermit()),false);

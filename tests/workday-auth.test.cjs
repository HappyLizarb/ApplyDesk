const assert=require('node:assert/strict');
const {annotateWorkdayAuth,workdayAuthRequest}=require('../browser/workday-auth.cjs');
const url='https://example.wd5.myworkdayjobs.com/en-US/Careers/job/US/Engineer_JR123/apply/applyManually';
const snapshot={url,workday_auth:{root:'/en-US/Careers'},fields:[
  {field_id:'email',form_id:'f',type:'text',label:'Email Address'},
  {field_id:'password',form_id:'f',type:'password',label:'Password'},
  {field_id:'repeat',form_id:'f',type:'password',label:'Verify New Password'},
  {field_id:'consent',form_id:'f',type:'checkbox',label:'I agree'}],buttons:[
  {field_id:'header',text:'Sign In',type:'submit',form_id:null},
  {field_id:'create',text:'Create Account',type:'submit',form_id:'f'}]};
annotateWorkdayAuth(snapshot);
const button=snapshot.buttons[1];
assert.equal(button.auth_form,true);
assert.equal(snapshot.fields[3].workday_consent,true);
assert.equal(new URL(button.form_action).pathname,'/en-US/Careers/register');
const permit=()=>({workday:true,url:button.form_action,expires:Date.now()+10000,used:false,username:'fictional@example.test',password:'synthetic-password',redirect:new URL(url).pathname});
const request=(body,endpoint=button.form_action)=>({url:()=>endpoint,method:()=>'POST',postData:()=>new URLSearchParams(body).toString()});
const body={username:'fictional@example.test',password:'synthetic-password',successRedirectUri:new URL(url).pathname};
const once=permit();assert.equal(workdayAuthRequest(request(body),once),true);assert.equal(workdayAuthRequest(request(body),once),false);
assert.equal(workdayAuthRequest(request({...body,resume:'unapproved'}),permit()),false);
assert.equal(workdayAuthRequest(request({...body,successRedirectUri:'/other'}),permit()),false);
assert.equal(workdayAuthRequest(request({...body,password:'changed'}),permit()),false);
assert.equal(workdayAuthRequest(request(body,button.form_action.replace('example.','other.')),permit()),false);
console.log('Workday account request is scoped, payload-bound and one-use.');

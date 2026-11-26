// Identify only the employer's native account form, retaining its real destination.
function successFactorsAuthMetadata(identity) {
  if (location.origin !== 'https://career4.successfactors.com' || !['/career','/careers'].includes(location.pathname)) return null;
  const form = document.querySelector('form#careerform');
  if (!form || form.method.toLowerCase() !== 'post') return null;
  const value = name=>form.querySelector(`[name="${name}"]`)?.value;
  const target = new URL(form.action), requisition = value('career_job_req_id');
  if (target.origin !== location.origin || target.pathname !== '/career' || [...target.searchParams.keys()].some(key=>key !== '_s.crb')
      || value('career_company') !== 'supermicro' || !/^[0-9]+$/.test(requisition)) return null;
  const register = !!form.querySelector('#fbclc_createAccountButton');
  const passwords = [...form.querySelectorAll('input[type="password"]')];
  if (passwords.length !== (register ? 2 : 1)) return null;
  const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(node=>node.getClientRects().length);
  const dialog = dialogs.length===1 ? dialogs[0] : null;
  const accept = dialog && [...dialog.querySelectorAll('button')].filter(node=>node.name==='Accept' && node.type==='button');
  return {privacy_dialog:dialog ? {text:dialog.innerText,accept:accept.length===1 ? identity(accept[0]) : null} : null,tenant:'supermicro',requisition,form_id:identity(form),action:form.action,
    privacy_link:register && document.querySelector('#dataPrivacyId') ? identity(document.querySelector('#dataPrivacyId')) : null,
    kind:register?'create_account':'login',privacy_accepted:!!value('fbclc_dpcsId')};
}

// Account navigation retains the verified source posting without claiming a job form is ready.
function annotateSuccessFactorsAuth(snapshot, state) {
  const meta = snapshot.successfactors_auth, binding = state.successFactorsBinding;
  if (!meta || !binding || meta.tenant !== binding.tenant || meta.requisition !== binding.requisition) return;
  snapshot.authentication_entry = {...binding,provider:'successfactors'};
  annotateReviewedPrivacy(snapshot,meta);
  const labels = {username:'Email address',password:'Password',fbclc_userName:'Email address',fbclc_emailConf:'Confirm email',
    fbclc_pwd:'Password',fbclc_pwdConf:'Confirm password',fbclc_fName:'First name',fbclc_lName:'Last name',fbclc_country:'Country'};
  for (const field of snapshot.fields.filter(field=>field.form_id===meta.form_id)) {
    if (labels[field.name]) field.label = labels[field.name];
    field.account_notification = ['fbclc_emailEnabled','fbclc_campaignEmailEnabled'].includes(field.name);
  }
  const buttons = snapshot.buttons.filter(button=>button.form_id===meta.form_id && button.type==='submit'
    && (meta.kind==='login' ? button.text==='Sign In' : button.text==='Create Account'));
  if (buttons.length===1) Object.assign(buttons[0],{auth_form:true,auth_widget:'successfactors',form_action:meta.action,form_method:'POST'});
  if (meta.kind === 'create_account' && meta.privacy_link && !meta.privacy_accepted) annotateAccountControl(snapshot,{
    field_id:meta.privacy_link,text:'Read and accept the data privacy statement.',type:'button',disabled:false,
    action:'advance',navigation_kind:'account_privacy',auth_widget:'successfactors'});
  if (meta.kind !== 'login') return;
  const links = snapshot.links.filter(link=>link.text==='Create an account' && validRegistrationLink(link.href,meta));
  if (links.length===1) annotateAccountControl(snapshot,{field_id:links[0].id,text:'Create Account',type:'button',disabled:false,
    action:'advance',navigation_kind:'account_entry',auth_widget:'successfactors'});
}

// The observed account link must preserve both employer tenant and requisition.
function validRegistrationLink(href, meta) {
  try {
    const url = new URL(href);
    return url.origin==='https://career4.successfactors.com' && url.pathname==='/career'
      && url.searchParams.get('company')===meta.tenant && url.searchParams.get('career_job_req_id')===meta.requisition
      && url.searchParams.get('login_ns')==='register' && url.searchParams.get('career_ns')==='job_application';
  } catch { return false; }
}
module.exports = {successFactorsAuthMetadata, annotateSuccessFactorsAuth};

// Bind the account POST to every current native form value, including server-issued hidden context.
async function successFactorsAuthPermit(page, snapshot, button, expires) {
  const meta = snapshot.successfactors_auth;
  if (!meta || meta.form_id !== button.form_id || meta.action !== button.form_action
      || (meta.kind === 'create_account' && !meta.privacy_accepted)) throw new Error('The employer account prerequisites are incomplete.');
  const fields = await page.evaluate(id=>{
    const button=globalThis.__applyDesk.elements.get(id),form=button?.form;
    if (!form || form.id!=='careerform') throw new Error('The native account form changed.');
    const values=[...new FormData(form,button)];
    if (button.id==='fbclc_createAccountButton') {
      if (button.getAttribute('onclick')!=="setField('userMsgReq','false');return submitCreateAccount();")
        throw new Error('The employer signup handler changed.');
      for (const pair of values) if (pair[0]==='userMsgReq') pair[1]='false';
    }
    if (values.some(([,value])=>typeof value!=='string')) throw new Error('An account form cannot upload documents.');
    return values;
  },button.field_id);
  return {url:meta.action,method:'POST',expires,successfactors:true,used:false,fields,pageUrl:snapshot.url};
}

// An account permit cannot be reused for a different hidden action or submitted profile.
function successFactorsAuthRequest(request, permit) {
  if (!permit?.successfactors || permit.used || Date.now()>=permit.expires) return false;
  try {
    const frame=request.frame();
    if (request.method()!=='POST' || request.url()!==permit.url || frame.url()!==permit.pageUrl
        || frame!==frame.page().mainFrame()) return false;
    const values=[...new URLSearchParams(request.postData() || '')];
    if (JSON.stringify(values)!==JSON.stringify(permit.fields)) return false;
    permit.used=true;return true;
  } catch {return false;}
}
module.exports.successFactorsAuthPermit=successFactorsAuthPermit;
module.exports.successFactorsAuthRequest=successFactorsAuthRequest;

// Reuse native role=button controls rather than creating a duplicate opaque field identity.
function annotateAccountControl(snapshot, control) {
  const matches = snapshot.buttons.filter(button=>button.field_id===control.field_id);
  if (matches.length>1) return;
  if (matches.length===1) Object.assign(matches[0],control);
  else snapshot.buttons.push(control);
}

// Only the reviewed recruiting statement may be accepted automatically; changed wording stops the flow.
function annotateReviewedPrivacy(snapshot, meta) {
  const dialog=meta.privacy_dialog;
  if (!dialog?.accept || meta.kind!=='create_account') return;
  const hash=require('node:crypto').createHash('sha256').update(dialog.text.replace(/\s+/g,' ').trim()).digest('hex');
  if (hash!=='c447b39b87771038ac755dfdab71d636ee50cfab7d5c1fe11bcd4a2844b41b12') return;
  annotateAccountControl(snapshot,{field_id:dialog.accept,text:'Accept',type:'button',disabled:false,
    action:'advance',navigation_kind:'account_privacy',auth_widget:'successfactors',reviewed_privacy:true});
}

// Read only the authentication route published by the Workday application itself.
function workdayAuthMetadata() {
  if (!/^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname)) return null;
  const root = window.recruiting?.meta?.()?.rootPath, site = window.workday?.siteId;
  if (typeof root !== 'string' || !/^\/[a-z]{2}-[A-Z]{2}\/[\w-]+$/.test(root)
      || root.split('/').pop() !== site || !location.pathname.startsWith(root + '/job/')) return null;
  return {root, site};
}

// SPA authentication fields share a real form, but its HTML action is not the API route.
function annotateWorkdayAuth(snapshot) {
  const meta = snapshot.workday_auth;
  if (!meta) return;
  const url = new URL(snapshot.url), forms = new Set(snapshot.fields.filter(f=>f.type==='password').map(f=>f.form_id));
  if (forms.size !== 1 || forms.has(null)) return;
  const form = [...forms][0], fields = snapshot.fields.filter(f=>f.form_id===form);
  const passwords = fields.filter(f=>f.type==='password');
  const action = passwords.length === 2 ? 'create_account' : passwords.length === 1 ? 'login' : '';
  const label = action === 'create_account' ? 'Create Account' : 'Sign In';
  const buttons = snapshot.buttons.filter(b=>b.form_id===form && b.text===label && b.type==='submit');
  if (!action || buttons.length !== 1 || fields.some(f=>!['text','email','password','checkbox'].includes(f.type))) return;
  const target = url.origin + meta.root + (action==='create_account' ? '/register?redirect='+encodeURIComponent(url.pathname+url.search) : '/login');
  const selected = buttons[0];
  Object.assign(selected,{form_action:target,form_method:'POST',auth_form:true,auth_widget:'workday'});
  for(const field of fields)Object.assign(field,{form_action:target,form_method:'POST',workday_consent:field.type==='checkbox'&&field.label==='I agree'});
}

// Snapshot-bound credentials authorize exactly one matching Workday authentication request.
async function workdayAuthPermit(page, snapshot, button, expires) {
  const values = await page.evaluate(id => {
    const button=globalThis.__applyDesk.elements.get(id), form=button?.form;
    if(!form)throw new Error('The account form changed.');
    form.addEventListener('submit',event=>event.preventDefault(),{capture:true,once:true});
    return {username:form.querySelector('input[autocomplete="email"]')?.value,
      password:form.querySelector('input[type="password"]')?.value};
  },button.field_id);
  if(!values.username || !values.password)throw new Error('The account fields are incomplete.');
  return {url:button.form_action,method:'POST',expires,workday:true,used:false,...values,
    redirect:new URL(snapshot.url).pathname+new URL(snapshot.url).search};
}

// No employer form data or extra destination can travel under an account-creation permit.
function workdayAuthRequest(request, permit) {
  if(!permit?.workday || permit.used || Date.now()>=permit.expires || request.method()!=='POST' || request.url()!==permit.url)return false;
  const data=new URLSearchParams(request.postData()||'');
  const keys=[...data.keys()];
  if(new Set(keys).size!==keys.length || keys.some(key=>!['username','password','successRedirectUri','sessionSecureToken'].includes(key)))return false;
  if(data.get('username')!==permit.username || data.get('password')!==permit.password)return false;
  if(data.has('successRedirectUri') && data.get('successRedirectUri')!==permit.redirect)return false;
  permit.used=true;
  return true;
}

// Workday puts an accessible click surface over its aria-hidden native submit button.
async function workdayAuthClickTarget(element, button) {
  const target = await element.evaluateHandle((native, label) => {
    if (native.tagName !== 'BUTTON' || native.type !== 'submit' || !native.form
        || native.getAttribute('aria-hidden') !== 'true') return native;
    const proxies = [...native.parentElement.children].filter(node =>
      node.getAttribute('data-automation-id') === 'click_filter' && node.getAttribute('role') === 'button'
      && (node.getAttribute('aria-label') === label || (label === 'Sign In'
        && native.getAttribute('data-automation-id') === 'signInSubmitButton' && node.getAttribute('aria-label') === 'Submit'))
      && node.closest('form') === native.form);
    return proxies.length === 1 ? proxies[0] : native;
  }, button.text);
  return target.asElement();
}

module.exports={workdayAuthClickTarget,workdayAuthMetadata,annotateWorkdayAuth,workdayAuthPermit,workdayAuthRequest};

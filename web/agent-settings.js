import {state,h,api,refresh,busy,task,toast} from './ui.js';

export const apiProviders={openai:'OpenAI',anthropic:'Anthropic',google:'Google Gemini',xai:'Grok (xAI)',cursor:'Cursor'};
let pendingConnection=null;

// Account setup is separate from the single-agent picker in the chat composer.
export function renderAgentSettings(root) {
  delete root.dataset.accountFingerprint;
  root.innerHTML=`<header class="page-head"><h1>Account setup</h1><button id="back-to-chat">Back to chat</button></header>
    <div class="connection-setup"><section class="panel connection-overview"><div><label for="connection-source">Use for agent</label><select id="connection-source"><option value="chatgpt">ChatGPT account</option>${Object.entries(apiProviders).filter(([id])=>id!=='cursor').map(([id,label])=>`<option value="${id}_api">${label} API key</option>`).join('')}</select><p id="connection-status" class="muted small" role="status"></p><p id="connection-error" class="danger small" role="status"></p></div><div class="actions"><button id="api-model-refresh" data-command="models">Refresh models</button></div></section>
    <div class="account-grid"><section class="panel provider-card"><div class="panel-heading"><h2>ChatGPT</h2></div><div class="account-connection-row"><div id="account-picker"></div><div id="account-actions" class="actions"></div></div><div id="account-status" role="status"></div><p class="muted small account-note">Uses your ChatGPT plan. Requests share its usage limits.</p></section>${apiKeySection()}</div>
    <section class="panel"><label class="agent-auto"><input type="checkbox" name="automatic">Automatically process queued drafts</label></section></div>`;
  root.querySelector('#back-to-chat').onclick=()=>document.querySelector('#toggle-assistant').click();
  bindApiSettings(root);
  root.querySelector('.connection-setup').onclick=event=>{
    const button=event.target.closest('[data-command]');
    if(button)runAccountAction(button,button.dataset.command,button.dataset.accountId);
  };
  root.querySelector('#account-picker').onchange=event=>runAccountAction(event.target,'select',event.target.value);
  root.querySelector('[name=automatic]').onchange=async event=>{
    const checkbox=event.target;checkbox.disabled=true;
    try {await api('orchestrator/settings',{roles:state.data.orchestrator.settings.roles,automatic:checkbox.checked});await refresh();}
    catch(error){toast(error.message,true);}
    finally {updateAccount(root);}
  };
  updateAccount(root);
}

// Polling changes account badges only; password entry and focused controls remain intact.
export function updateAccount(root=document.querySelector('#main')) {
  if(!root.querySelector('#account-status'))return;
  const {account,signin,active,settings}=state.data.orchestrator;
  updateApiSettings(root);
  const automatic=root.querySelector('[name=automatic]');
  automatic.checked=settings.automatic;automatic.disabled=!account.connected||!!active;
  const fingerprint=JSON.stringify([account.accounts,account.chatgpt_active,account.source,account.active,account.error,account.notice,signin]);
  if(root.dataset.accountFingerprint===fingerprint)return;
  root.dataset.accountFingerprint=fingerprint;
  const selected=account.accounts.find(item=>item.id===(account.chatgpt_active||account.active));
  const waiting=['waiting','connecting'].includes(signin.status);
  const status=waiting?'Complete sign-in in your browser.':selected?'':'Not connected';
  root.querySelector('#account-status').innerHTML=`${status?`<p>${status}</p>`:''}${signin.error?`<p class="danger">${h(signin.error)}</p>`:''}${account.notice?`<p class="muted small">${h(account.notice)}</p>`:''}`;
  const refreshModels=!account.source||account.source==='chatgpt'?'<button id="refresh-models" data-command="models">Refresh models</button>':'';
  root.querySelector('#account-actions').innerHTML=waiting?'<button id="cancel-signin" data-command="cancel-signin">Cancel sign-in</button>':selected
    ?`<button id="reconnect-chatgpt" data-command="connect" data-account-id="${h(selected.id)}">Reconnect</button>${refreshModels}<button class="danger" id="disconnect-chatgpt" data-command="disconnect">Disconnect</button>`
    :'<button class="primary" id="connect-chatgpt" data-command="connect" data-account-id="">Continue with ChatGPT</button>';
  root.querySelector('#account-picker').innerHTML=account.accounts.length?`<label class="account-select">Account<select id="chatgpt-account">${!selected?'<option value="" selected disabled>Choose an account</option>':''}${account.accounts.map(row=>`<option value="${h(row.id)}" ${row.id===selected?.id?'selected':''}>${h(row.label)}</option>`).join('')}</select></label><button id="add-chatgpt-account" data-command="connect" data-account-id="">Add account</button>`:'';
}

// All account controls refresh server state after the selected action completes.
function runAccountAction(control,command,accountId) {
  const request=['models','disconnect'].includes(command)?task:api;
  return busy(control,async()=>{
    await request('orchestrator/'+command,accountId===undefined?{}:{account_id:accountId});
    await refresh();
  });
}

// One shared panel presents saved connections as editable rows, with no returned secrets.
function apiKeySection() {
  const saved=state.data.orchestrator.account.api_keys_saved||{};
  const providers=Object.keys(apiProviders).filter(name=>saved[name]);
  if(!providers.length)providers.push('openai');
  return `<section class="panel api-keys-panel"><div class="panel-heading"><h2>API Keys</h2></div><div id="api-key-rows">${providers.map(apiKeyRow).join('')}</div><button type="button" id="add-api-key" class="quiet profile-add">Add API key</button><p class="field-help">Keys stay in macOS Keychain. Usage is billed by the selected provider.</p></section>`;
}

// Native provider and password controls stay on one compact row when space permits.
function apiKeyRow(provider) {
  return `<form class="api-key-row" data-provider="${provider}"><label class="api-provider">Provider<select name="provider" aria-label="API provider">${Object.entries(apiProviders).map(([id,label])=>`<option value="${id}" ${id===provider?'selected':''}>${h(label)}</option>`).join('')}</select></label><label class="api-secret">API key<input name="key" type="password" aria-label="API key" autocomplete="new-password" spellcheck="false" placeholder="Enter API key" maxlength="1000"></label><div class="actions"><button type="submit" class="primary">Save and use</button><button type="button" data-remove-key class="quiet danger">Remove</button></div><span data-key-status class="muted small" role="status"></span></form>`;
}

// Changing a row's provider clears its draft key so it cannot be sent to another service.
export function bindApiSettings(root) {
  root.querySelector('#connection-source').onchange=event=>changeConnection(root,{source:event.target.value});
  root.querySelector('#add-api-key').onclick=()=>{
    const used=[...root.querySelectorAll('[data-provider]')].map(row=>row.dataset.provider);
    const next=Object.keys(apiProviders).find(provider=>!used.includes(provider));
    if(!next)return;
    const container=root.querySelector('#api-key-rows');
    container.insertAdjacentHTML('beforeend',apiKeyRow(next));
    bindKeyRow(root,container.lastElementChild);updateApiSettings(root);
  };
  root.querySelectorAll('[data-provider]').forEach(row=>bindKeyRow(root,row));
}

// Each row resolves its selected provider at the moment of an explicit action.
function bindKeyRow(root,row) {
  const input=row.querySelector('input');
  row.onsubmit=event=>{
    event.preventDefault();
    const provider=row.dataset.provider,key=input.value.trim();
    if(pendingConnection||provider==='cursor')return;
    if(key){input.value='';return changeConnection(root,{provider,key});}
    if(state.data.orchestrator.account.api_keys_saved?.[provider])return changeConnection(root,{source:provider+'_api'});
    toast('Enter an API key first.',true);
  };
  row.querySelector('select').onchange=event=>{
    row.dataset.provider=event.target.value;input.value='';updateApiSettings(root);
  };
  row.querySelector('[data-remove-key]').onclick=async()=>{
    if(pendingConnection)return;
    const provider=row.dataset.provider;
    if(state.data.orchestrator.account.api_keys_saved?.[provider]&&!await changeConnection(root,{provider,remove:true}))return;
    row.remove();updateApiSettings(root);
  };
}

// Polling preserves focused controls and password drafts while refreshing saved-key badges.
export function updateApiSettings(root) {
  const account=state.data.orchestrator.account,source=root.querySelector('#connection-source');
  if(!source)return;
  const provider=account.source?.replace('_api',''),label=apiProviders[provider];
  source.value=account.source||'chatgpt';source.disabled=!!pendingConnection;
  const rows=[...root.querySelectorAll('[data-provider]')],used=rows.map(row=>row.dataset.provider);
  rows.forEach(row=>updateKeyRow(row,account,used));
  root.querySelector('#add-api-key').disabled=!!pendingConnection||rows.length>=Object.keys(apiProviders).length;
  root.querySelector('#api-model-refresh').hidden=!label||!account.connected;
  root.querySelector('#api-model-refresh').disabled=!!pendingConnection;
  root.querySelector('#connection-status').textContent=account.connected?'':label?`Add your ${label} API key below`:'Connect ChatGPT below';
  root.querySelector('#connection-error').textContent=account.error||'';
}

// Cursor stays visible as requested, with an honest explanation instead of a nonworking save.
function updateKeyRow(row,account,used) {
  const name=row.dataset.provider,saved=account.api_keys_saved?.[name]??(name==='openai'&&account.api_key_saved);
  const unavailable=name==='cursor';
  row.querySelector('[data-key-status]').textContent=unavailable?'Cursor runs its own agents; direct model connection is unavailable.':pendingConnection?.provider===name?(pendingConnection.remove?'Removing…':'Checking key…'):saved?'Key saved':'No key saved';
  row.querySelector('[type=submit]').disabled=!!pendingConnection||unavailable;
  row.querySelector('[data-remove-key]').disabled=!!pendingConnection;
  row.querySelector('input').disabled=!!pendingConnection||unavailable;
  row.querySelector('select').disabled=!!pendingConnection;
  row.querySelectorAll('option').forEach(option=>option.disabled=option.value!==name&&used.includes(option.value));
}

// Serialize explicit connection changes and report success before removing a saved row.
async function changeConnection(root,{provider,key,source,remove}) {
  if(pendingConnection)return false;
  pendingConnection={provider,remove};updateApiSettings(root);
  try {
    if(key)await task('orchestrator/api-key',{provider,key});
    else if(remove)await api('orchestrator/api-key/remove',{provider});
    else await api('orchestrator/source',{source});
    await refresh();
    if(key)toast(`${apiProviders[provider]} key saved. Work remains paused.`);
    return true;
  } catch(error){toast(error.message,true);return false;}
  finally {pendingConnection=null;updateApiSettings(root);}
}

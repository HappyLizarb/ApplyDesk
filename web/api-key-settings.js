import {state,h,api,refresh,task,toast} from './ui.js';

export const apiProviders={openai:'OpenAI',anthropic:'Anthropic',google:'Google Gemini',xai:'Grok (xAI)',cursor:'Cursor'};
let pendingConnection=null;

// One shared panel presents saved connections as editable rows, with no returned secrets.
export function apiKeySection() {
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
    if(next)root.querySelector('#api-key-rows').insertAdjacentHTML('beforeend',apiKeyRow(next));
    bindRows(root);updateApiSettings(root);
  };
  bindRows(root);
}

// Each row resolves its selected provider at the moment of an explicit action.
function bindRows(root) {
  root.querySelectorAll('[data-provider]').forEach(row=>{
    row.onsubmit=event=>{event.preventDefault();saveApiKey(root,row);};
    row.querySelector('select').onchange=event=>{
      row.dataset.provider=event.target.value;row.querySelector('input').value='';updateApiSettings(root);
    };
    row.querySelector('[data-remove-key]').onclick=async()=>{
      const provider=row.dataset.provider;
      if(state.data.orchestrator.account.api_keys_saved?.[provider]) {
        if(!await changeConnection(root,{provider,remove:true}))return;
      }
      if(pendingConnection)return;
      row.remove();updateApiSettings(root);
    };
  });
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
async function changeConnection(root,change) {
  if(pendingConnection)return false;
  pendingConnection=change;updateApiSettings(root);
  try {
    const path=change.remove?'orchestrator/api-key/remove':'orchestrator/source';
    await api(path,change.remove?{provider:change.provider}:{source:change.source});await refresh();
    return true;
  } catch(error){toast(error.message,true);return false;}
  finally {pendingConnection=null;updateApiSettings(root);}
}

// Save only the chosen provider's key; an empty entry can activate its already saved key.
async function saveApiKey(root,row) {
  if(pendingConnection||row.dataset.provider==='cursor')return;
  const provider=row.dataset.provider,input=row.querySelector('input'),key=input.value.trim();
  if(!key) {
    if(state.data.orchestrator.account.api_keys_saved?.[provider])await changeConnection(root,{source:provider+'_api'});
    else toast('Enter an API key first.',true);
    return;
  }
  pendingConnection={provider};input.value='';updateApiSettings(root);
  try {await task('orchestrator/api-key',{key,provider});await refresh();toast(`${apiProviders[provider]} key saved. Work remains paused.`);}
  catch(error){toast(error.message,true);}
  finally {pendingConnection=null;updateApiSettings(root);}
}

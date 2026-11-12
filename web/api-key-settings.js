import {state,api,refresh,task,toast} from './ui.js';

export const apiProviders={openai:'OpenAI',anthropic:'Anthropic',google:'Google'};
let pendingConnection=null;

// Each provider keeps its own password entry; saved secrets never return to the browser.
export function apiKeySection() {
  return Object.entries(apiProviders).map(([provider,label])=>`<section class="panel provider-card" data-provider="${provider}">
    <div class="provider-card-heading"><h2>${label}${provider==='google'?' Gemini':' API'}</h2><span data-key-status class="muted small" role="status"></span></div>
    <form><label for="${provider}-api-key">API key<input id="${provider}-api-key" name="key" type="password" autocomplete="new-password" spellcheck="false" placeholder="Enter your ${label} API key" maxlength="1000" required></label>
    <p class="field-help">Stored in macOS Keychain. API usage is billed to your ${label} account.</p>
    <div class="actions"><button type="submit" class="primary">Save and use ${label}</button><button type="button" data-remove-key>Remove key</button></div></form></section>`).join('');
}

// Source changes pause work so the provider and billing choice remain explicit.
export function bindApiSettings(root) {
  root.querySelector('#connection-source').onchange=event=>changeConnection(root,{source:event.target.value});
  root.querySelectorAll('[data-provider]').forEach(card=>{
    const provider=card.dataset.provider;
    card.querySelector('form').onsubmit=event=>{event.preventDefault();saveApiKey(root,card,provider);};
    card.querySelector('[data-remove-key]').onclick=()=>changeConnection(root,{provider,remove:true});
  });
}

// Polling updates badges in place and leaves unfinished password entries untouched.
export function updateApiSettings(root) {
  const account=state.data.orchestrator.account,source=root.querySelector('#connection-source');
  if(!source)return;
  const provider=account.source?.replace('_api',''),label=apiProviders[provider];
  source.value=account.source||'chatgpt';source.disabled=!!pendingConnection;
  root.querySelectorAll('[data-provider]').forEach(card=>{
    const name=card.dataset.provider,saved=account.api_keys_saved?.[name]??(name==='openai'&&account.api_key_saved);
    card.querySelector('[data-key-status]').textContent=pendingConnection?.provider===name?(pendingConnection.remove?'Removing…':'Checking key…'):saved?'Key saved':'No key saved';
    card.querySelector('[type=submit]').disabled=!!pendingConnection;
    card.querySelector('[data-remove-key]').disabled=!!pendingConnection||!saved;
    card.querySelector('input').disabled=!!pendingConnection;
  });
  root.querySelector('#api-model-refresh').hidden=!label||!account.connected;
  root.querySelector('#api-model-refresh').disabled=!!pendingConnection;
  root.querySelector('#connection-status').textContent=account.connected?`Using ${label?label+' API':'ChatGPT'}`:label?`Add your ${label} API key below`:'Connect ChatGPT below';
  root.querySelector('#connection-error').textContent=account.error||'';
}

// Serialize connection changes to avoid racing the provider selection with key validation.
async function changeConnection(root,change) {
  if(pendingConnection)return;
  pendingConnection=change;updateApiSettings(root);
  try {
    const path=change.remove?'orchestrator/api-key/remove':'orchestrator/source';
    await api(path,change.remove?{provider:change.provider}:{source:change.source});await refresh();
  } catch(error){toast(error.message,true);}
  finally {pendingConnection=null;updateApiSettings(root);}
}

// A submitted key is cleared immediately and is sent only to the local connection endpoint.
async function saveApiKey(root,card,provider) {
  if(pendingConnection)return;
  const input=card.querySelector('input'),key=input.value.trim();
  if(!key)return;
  pendingConnection={provider};input.value='';updateApiSettings(root);
  try {await task('orchestrator/api-key',{key,provider});await refresh();toast(`${apiProviders[provider]} key saved. Work remains paused.`);}
  catch(error){toast(error.message,true);}
  finally {pendingConnection=null;updateApiSettings(root);}
}

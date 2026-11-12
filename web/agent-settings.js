import {state,h,api,refresh,busy,task,toast} from './ui.js';
import {apiProviders,apiKeySection,bindApiSettings,updateApiSettings} from './api-key-settings.js';

export const roles={master:'Master Agent',application:'Application Agent',document_reviewer:'Document Reviewer',application_auditor:'Application Auditor'};

// Account setup is separate from the four-agent picker in the chat composer.
export function renderAgentSettings(root) {
  delete root.dataset.accountFingerprint;
  root.innerHTML=`<header class="page-head"><h1>Account setup</h1><button id="back-to-chat">Back to chat</button></header>
    <div class="connection-setup"><section class="panel connection-overview"><div><label for="connection-source">Use for agents</label><select id="connection-source"><option value="chatgpt">ChatGPT account</option>${Object.entries(apiProviders).map(([id,label])=>`<option value="${id}_api">${label} API key</option>`).join('')}</select><p id="connection-status" class="muted small" role="status"></p><p id="connection-error" class="danger small" role="status"></p></div><div class="actions"><button id="api-model-refresh">Refresh models</button></div></section>
    <div class="account-grid"><section class="panel provider-card"><div class="panel-heading"><h2>ChatGPT</h2><div id="account-actions" class="actions"></div></div><div id="account-status"></div><div id="account-picker"></div><p class="muted small account-note">Uses your ChatGPT plan. Requests share its usage limits.</p></section>${apiKeySection()}</div>
    <section class="panel"><label class="agent-auto"><input type="checkbox" name="automatic">Automatically process queued drafts</label></section></div>`;
  root.querySelector('#back-to-chat').onclick=()=>document.querySelector('#toggle-assistant').click();
  bindApiSettings(root);
  root.querySelector('#api-model-refresh').onclick=event=>busy(event.target,async()=>{await task('orchestrator/models',{});await refresh();});
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
  root.querySelector('#account-status').innerHTML=`<p>${waiting?'Complete sign-in in your browser.':selected?`Connected · ${h(selected.label)}`:'Not connected'}</p>${signin.error?`<p class="danger">${h(signin.error)}</p>`:''}${account.notice?`<p class="muted small">${h(account.notice)}</p>`:''}`;
  root.querySelector('#account-actions').innerHTML=waiting?'<button id="cancel-signin">Cancel sign-in</button>':`${selected?'<button id="reconnect-chatgpt">Reconnect</button><button id="refresh-models">Refresh models</button><button class="danger" id="disconnect-chatgpt">Disconnect</button>':'<button class="primary" id="connect-chatgpt">Continue with ChatGPT</button>'}`;
  root.querySelector('#account-picker').innerHTML=account.accounts.length?`<label class="account-select">Account<select id="chatgpt-account">${!selected?'<option value="" selected disabled>Choose an account</option>':''}${account.accounts.map(row=>`<option value="${h(row.id)}" ${row.id===selected?.id?'selected':''}>${h(row.label)}</option>`).join('')}</select></label><button id="add-chatgpt-account">Add account</button>`:'';
  if(account.source&&account.source!=='chatgpt')root.querySelector('#refresh-models')?.remove();
  bindAccountActions(root,selected?.id||'');
}

// Existing ChatGPT registrations stay available when API-key billing is selected.
function bindAccountActions(root,identity) {
  const connect=async id=>{await api('orchestrator/connect',{account_id:id});await refresh();};
  root.querySelector('#connect-chatgpt')?.addEventListener('click',event=>busy(event.target,()=>connect(identity)));
  root.querySelector('#reconnect-chatgpt')?.addEventListener('click',event=>busy(event.target,()=>connect(identity)));
  root.querySelector('#add-chatgpt-account')?.addEventListener('click',event=>busy(event.target,()=>connect('')));
  root.querySelector('#cancel-signin')?.addEventListener('click',event=>busy(event.target,async()=>{await api('orchestrator/cancel-signin',{});await refresh();}));
  root.querySelector('#disconnect-chatgpt')?.addEventListener('click',event=>busy(event.target,async()=>{await task('orchestrator/disconnect',{});await refresh();}));
  root.querySelector('#refresh-models')?.addEventListener('click',event=>busy(event.target,async()=>{await task('orchestrator/models',{});await refresh();}));
  root.querySelector('#chatgpt-account')?.addEventListener('change',event=>busy(event.target,async()=>{await api('orchestrator/select',{account_id:event.target.value});await refresh();}));
}

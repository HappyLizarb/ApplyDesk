import {state,h,api,busy,refresh,icon,toast,modal} from './ui.js';
import {apiProviders} from './api-key-settings.js';
import {roles as roleNames} from './agent-settings.js';
import {agentMenu,initializeAgentMenu,updateAgentMenu} from './composer-agents.js';
import {submitComposer,updateComposeAction} from './composer-send.js';

let open=false,lastMessages='',lastRuns='';

// Keep one conversation surface alive across navigation so unsent text survives.
export function initializeAssistant() {
  const panel=document.querySelector('#assistant-panel');
  panel.innerHTML=`<header class="assistant-head"><div><h2>ApplyDesk chat</h2><span id="assistant-status" class="sr-only" role="status"></span></div><div class="actions"><button id="reset-assistant" type="button" aria-label="Delete and restart chat">${icon('restart')}<span>New chat</span></button><button id="close-assistant" aria-label="Close chat">${icon('close')}</button></div></header><div id="assistant-connection"></div><div id="assistant-messages" role="log" aria-label="Chat messages"></div><details class="agent-activity"><summary>Agent activity <span id="activity-count"></span></summary><div id="agent-runs"></div></details><div id="assistant-error" role="status"></div><div id="application-run-status" role="status"></div><form id="assistant-form"><label class="sr-only" for="assistant-input">Message the master agent</label><textarea id="assistant-input" rows="2" placeholder="Ask, prepare drafts, or submit with a limit…" maxlength="20000"></textarea><div class="assistant-compose-actions"><a class="composer-account-link" href="#settings" aria-label="Account setup" title="Account setup">${icon('settings')}</a>${agentMenu()}<button type="submit" class="primary" id="send-assistant" aria-label="Send message">${icon('send')}</button></div></form>`;
  document.querySelector('#toggle-assistant').onclick=()=>toggleAssistant(!open);
  panel.querySelector('#close-assistant').onclick=()=>toggleAssistant(false);
  panel.querySelector('#reset-assistant').onclick=resetChat;
  initializeAgentMenu(panel);
  panel.querySelector('#assistant-input').oninput=updateComposeAction;
  panel.querySelector('#assistant-form').onsubmit=event=>{event.preventDefault();submitComposer();};
  panel.querySelector('#assistant-input').onkeydown=event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();submitComposer();}};
  document.querySelector('.sidebar').addEventListener('click',event=>{
    if(event.target.closest('[data-view],.brand'))toggleAssistant(false);
  });
  panel.addEventListener('click',event=>{
    if(event.target.closest('a[href^="#"]'))toggleAssistant(false);
  });
  window.addEventListener('hashchange',()=>toggleAssistant(false));
  window.addEventListener('workspace-updated',updateAssistant);
  updateAssistant();
}

// Chat replaces the visible page without discarding its forms or the conversation.
export function toggleAssistant(value=true) {
  open=value;document.body.classList.toggle('assistant-open',open);
  const panel=document.querySelector('#assistant-panel'),toggle=document.querySelector('#toggle-assistant');
  if(!open&&panel.contains(document.activeElement))toggle.focus();
  panel.hidden=!open;
  document.querySelector('#main').inert=open;
  toggle.setAttribute('aria-expanded',String(open));
  if(open)document.querySelector('#assistant-input').focus();
}

// Deleting a conversation is explicit; failed resets preserve messages and the unsent composer.
function resetChat() {
  const dialog=modal('Start a new chat?', '<div class="modal-body"><p>This deletes the current conversation and pauses active and queued agent work. Your saved profile, applications, and documents are kept.</p></div>', '<button id="cancel-chat-reset" type="button">Cancel</button><button id="confirm-chat-reset" class="danger" type="button">Delete and restart</button>');
  dialog.querySelector('#cancel-chat-reset').onclick=()=>dialog.close();
  dialog.querySelector('#confirm-chat-reset').onclick=event=>busy(event.currentTarget,async()=>{
    await api('orchestrator/reset',{});
    document.querySelector('#assistant-input').value='';
    await refresh();dialog.close();
    document.querySelector('#assistant-input').focus();
    toast('Chat cleared. Agents are paused.');
  });
}

// Poll incremental text separately from the composer, preserving focus and scroll position.
export function updateAssistant() {
  const engine=state.data?.orchestrator,panel=document.querySelector('#assistant-panel');if(!engine||!panel.querySelector('#assistant-status'))return;
  const activeRole=engine.current_run?.role;
  updateApplicationRun(panel,engine.application_runs||[]);
  panel.querySelector('#assistant-status').textContent=engine.paused?(engine.active?'Stopping agents…':'Paused'):activeRole?`${roleNames[activeRole]} working…`:engine.active?'Agents working…':engine.account.connected?`Connected to ${apiProviders[engine.account.source?.replace('_api','')]||'ChatGPT'}`:'Connect an account to begin';
  panel.querySelector('#assistant-connection').innerHTML=engine.account.connected?'':'<a class="assistant-connect" href="#settings">Open Account setup</a>';
  updateComposeAction();updateAgentMenu();
  panel.querySelector('#assistant-error').textContent=engine.error||'';
  const messages=panel.querySelector('#assistant-messages'),serialized=JSON.stringify(engine.messages);
  if(serialized!==lastMessages){
    const bottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<80;
    messages.innerHTML=engine.messages.length?engine.messages.map(message=>`<article class="chat-message ${h(message.role)}"><div class="chat-message-heading">${message.role==='user'?'You':'ApplyDesk'}${message.status!=='complete'?`<span>${h(message.status)}</span>`:''}</div><div class="chat-message-text">${h(message.text||'Thinking…')}</div></article>`).join(''):'<div class="chat-empty"><h3>What would you like to work on?</h3><p>Plan your next steps, ask about a job, or prepare queued drafts.</p></div>';
    if(bottom)messages.scrollTop=messages.scrollHeight;lastMessages=serialized;
  }
  const runs=JSON.stringify(engine.runs);if(runs!==lastRuns){panel.querySelector('#agent-runs').innerHTML=engine.runs.slice(-12).reverse().map(run=>`<div class="agent-run"><strong>${h(roleNames[run.role]||run.role)}</strong><span>${h(run.status)}</span><small>${h(run.model)}</small></div>`).join('')||'<p class="muted small">No agent runs yet.</p>';panel.querySelector('#activity-count').textContent=engine.runs.length||'';lastRuns=runs;}
}

// Show actual receipts and uncertain attempts without treating a model reply as submission proof.
function updateApplicationRun(panel,runs) {
  const root=panel.querySelector('#application-run-status'),run=runs.at(-1);
  if(!run){root.textContent='';return;}
  const sent=run.submitted_count||0;
  const uncertain=run.uncertain_count||0;
  const job=state.data.jobs.find(item=>item.id===run.current_job);
  root.textContent=`${run.status==='running'?'Applying':run.status==='complete'?'Run finished':'Run stopped'} · ${sent} submitted · limit ${run.limit}${uncertain?` · ${uncertain} uncertain`:''}${job?` · ${job.company} — ${job.title}`:''}`;
}

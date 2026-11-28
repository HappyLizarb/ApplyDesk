import {state,h,api,busy,refresh,icon,toast,modal} from './ui.js';
import {apiProviders} from './agent-settings.js';
import {initializeAgentMenu,updateAgentMenu} from './composer-agents.js';
import {dockApplicationWindow,updateApplicationWindow} from './application-window.js';

const roleNames={master:'ApplyDesk Agent'};
let lastMessages='',lastRuns='',pending=false,resumePending=false;

// Keep one conversation surface alive across navigation so unsent text survives.
export function initializeAssistant() {
  const panel=document.querySelector('#assistant-panel');
  panel.innerHTML=`<header class="assistant-head"><div><h2>ApplyDesk chat</h2><span id="assistant-status" class="sr-only" role="status"></span></div><div class="actions"><button id="reset-assistant" type="button" aria-label="Delete and restart chat">${icon('restart')}<span>New chat</span></button><button id="close-assistant" aria-label="Close chat">${icon('close')}</button></div></header>
    <div id="assistant-connection" hidden><a class="assistant-connect" href="#settings">Open Account setup</a></div>
    <div id="assistant-messages" role="log" aria-label="Chat messages"></div>
    <details class="agent-activity"><summary>Agent activity <span id="activity-count"></span></summary><div id="agent-runs"></div></details>
    <div id="assistant-error" role="status"></div>
    <button id="show-application" type="button" class="show-application" aria-label="Show the application workspace beside ApplyDesk" hidden>Show application workspace</button>
    <form id="assistant-form"><label class="sr-only" for="assistant-input">Message the agent</label>
    <textarea id="assistant-input" rows="1" placeholder="Ask, prepare drafts, or apply to jobs…" maxlength="20000"></textarea>
    <div class="assistant-compose-actions"><div class="composer-tools"><a class="composer-account-link" href="#settings" aria-label="Account setup" title="Account setup">${icon('settings')}</a><button type="button" id="auto-start-button" class="composer-timer-button" aria-label="Automatic start off" aria-haspopup="dialog">${icon('clock-off')}</button></div>
    <details id="composer-agents"><summary class="agent-menu-trigger" aria-label="Choose the agent model and thinking strength"><span id="composer-model-label"></span><span id="composer-thinking-label"></span>${icon('chevron-down')}</summary><div class="agent-menu-popover"><div id="composer-agent-rows"></div><p id="agent-menu-status" class="muted small" role="status"></p></div></details>
    <button type="submit" class="primary" id="send-assistant" aria-label="Send message">${icon('send')}</button></div></form>`;
  document.querySelector('#toggle-assistant').onclick=()=>toggleAssistant(panel.hidden);
  panel.querySelector('#close-assistant').onclick=()=>toggleAssistant(false);
  panel.querySelector('#reset-assistant').onclick=resetChat;
  panel.querySelector('#show-application').onclick=event=>busy(event.currentTarget,dockApplicationWindow);
  initializeAgentMenu(panel);
  panel.querySelector('#auto-start-button').onclick=showAutoStart;
  panel.querySelector('#assistant-input').oninput=updateComposeAction;
  panel.querySelector('#assistant-form').onsubmit=event=>{event.preventDefault();submitComposer();};
  panel.querySelector('#assistant-input').onkeydown=event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();submitComposer();}};
  document.addEventListener('click',event=>{
    if(event.target.closest('nav [data-view],#assistant-panel a[href^="#"]'))toggleAssistant(false);
  });
  window.addEventListener('hashchange',()=>toggleAssistant(false));
  window.addEventListener('workspace-updated',()=>updateAssistant(true));
}

// Chat replaces the visible page without discarding its forms or the conversation.
export function toggleAssistant(open=true) {
  document.body.classList.toggle('assistant-open',open);
  const panel=document.querySelector('#assistant-panel'),toggle=document.querySelector('#toggle-assistant');
  if(!open&&panel.contains(document.activeElement))toggle.focus();
  panel.hidden=!open;
  document.querySelector('#main').inert=open;
  toggle.setAttribute('aria-expanded',String(open));
  if(open){updateAssistant(false);panel.querySelector('#assistant-input').focus();}
}

// A shortcut opens an editable draft; only Send gives the agent an instruction.
export function composeInstruction(message) {
  toggleAssistant(true);
  const input=document.querySelector('#assistant-input');
  if(input.value.trim())return toast('Your unsent message is still in chat.');
  input.value=message;
  input.dispatchEvent(new Event('input',{bubbles:true}));
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
    toast('Chat cleared. The agent is paused.');
  });
}

// Follow new messages only during live updates. Opening chat restores the reading position.
function updateAssistant(followMessages) {
  const panel=document.querySelector('#assistant-panel'),engine=state.data?.orchestrator;
  if(!engine)return;
  updateApplicationWindow(panel,engine);
  if(!engine.paused)resumePending=false;
  if(panel.hidden)return;
  const activeRole=engine.current_run?.role;
  panel.querySelector('#assistant-status').textContent=engine.paused?(engine.active?'Stopping agent…':'Paused'):activeRole?`${roleNames[activeRole]} working…`:engine.active?'Agent working…':engine.account.connected?`Connected to ${apiProviders[engine.account.source?.replace('_api','')]||'ChatGPT'}`:'Connect an account to begin';
  panel.querySelector('#assistant-connection').hidden=engine.account.connected;
  updateComposeAction();updateAgentMenu();updateAutoStart();
  panel.querySelector('#assistant-error').textContent=engine.error||'';
  const messages=panel.querySelector('#assistant-messages'),serialized=JSON.stringify(engine.messages);
  if(serialized!==lastMessages){
    const bottom=followMessages&&messages.scrollHeight-messages.scrollTop-messages.clientHeight<80;
    messages.innerHTML=engine.messages.length?engine.messages.map(message=>`<article class="chat-message ${h(message.role)}"><div class="chat-message-heading">${message.role==='user'?'You':'ApplyDesk'}${message.status!=='complete'?`<span>${h(message.status)}</span>`:''}</div><div class="chat-message-text">${h(message.text||'Thinking…')}</div></article>`).join(''):'<div class="chat-empty"><h3>What would you like to work on?</h3><p>Plan your next steps, ask about a job, or prepare queued drafts.</p></div>';
    if(bottom)messages.scrollTop=messages.scrollHeight;lastMessages=serialized;
  }
  const runs=JSON.stringify(engine.runs);
  if(runs===lastRuns)return;
  lastRuns=runs;
  const groups=groupAgentRuns(engine.runs);
  panel.querySelector('#agent-runs').innerHTML=groups.slice(-12).reverse().map(run=>`<div class="agent-run"><strong>${h(roleNames[run.role]||'Previous agent run')}</strong><span>${h(run.status)}</span><small>${h(run.model)}${run.steps>1?` · ${run.steps} steps`:''}</small></div>`).join('')||'<p class="muted small">No agent runs yet.</p>';
  panel.querySelector('#activity-count').textContent=groups.length||'';
}

// One agent conversation can include many sequential model requests and tool steps.
export function groupAgentRuns(runs) {
  const groups=[],masters=new Map();
  for(const run of runs) {
    const previous=groups.at(-1);
    const key=run.role==='master'?(run.turn_id||(!previous?.turn_id&&previous?.role==='master'?previous.id:run.id)):null;
    const current=key&&masters.get(key);
    if(current) {
      current.steps++;
      current.status=run.status;
      current.model=run.model;
      current.finished_at=run.finished_at;
    } else {
      const group={...run,steps:1};
      groups.push(group);
      if(key)masters.set(key,group);
    }
  }
  return groups;
}


// Work includes queued chat and tool execution between individual model calls.
export function composerIsWorking(engine) {
  return !engine.paused&&(!!engine.active||engine.messages.some(message=>
    message.role==='user'&&['queued','running'].includes(message.status)));
}

// One accessible icon represents Send, Stop, or cancellation still in progress.
export function updateComposeAction() {
  const engine=state.data.orchestrator,button=document.querySelector('#send-assistant');
  const stopping=engine.paused&&!!engine.active,working=composerIsWorking(engine);
  const label=stopping?'Stopping agent':working?'Stop agent':resumePending?'Resume queued message':'Send message';
  if(button.title!==label)button.innerHTML=icon(working||stopping?'stop':'send');
  button.setAttribute('aria-label',label);button.title=engine.paused&&!stopping?'Send message and resume queued work':label;
  button.setAttribute('aria-busy',String(pending||stopping));
  const empty=!document.querySelector('#assistant-input').value.trim();
  button.disabled=pending||stopping||(!working&&(!engine.account.connected||(empty&&!resumePending)));
}

// Accepted text is cleared once; a failed resume can be retried without posting it again.
export async function submitComposer() {
  if(pending)return;
  const engine=state.data.orchestrator,input=document.querySelector('#assistant-input');
  const message=input.value.trim(),working=composerIsWorking(engine);
  if(engine.paused&&engine.active)return;
  if(!working&&(!engine.account.connected||(!message&&!resumePending)))return;
  pending=true;updateComposeAction();
  try {
    if(working)await api('orchestrator/control',{paused:true});
    else {
      if(message){
        await api('orchestrator/send',{message});
        if(input.value.trim()===message)input.value='';
      }
      if(engine.paused){resumePending=true;await api('orchestrator/control',{paused:false});resumePending=false;}
    }
    await refresh();
  } catch(error){toast(resumePending?`Message queued. Send again to resume queued work. ${error.message}`:error.message,true);}
  finally {pending=false;updateComposeAction();}
}

// Poll the icon without replacing an open interval form or its unsaved edits.
function updateAutoStart() {
  const button=document.querySelector('#auto-start-button');
  const timer=state.data.orchestrator.auto_start;
  const label=timer?.enabled?`Automatic start every ${timer.interval} ${timer.unit}`:'Automatic start off';
  if(button.title===label)return;
  button.innerHTML=icon(timer?.enabled?'clock':'clock-off');
  button.setAttribute('aria-label',label);button.title=label;
  button.classList.toggle('active',!!timer?.enabled);
}

// A single timer resumes unfinished work; it never collects another task or prompt.
function showAutoStart() {
  const timer=state.data.orchestrator.auto_start||{enabled:false,interval:5,unit:'hours'};
  const body=`<form id="auto-start-form" class="modal-body auto-start-fields"><label class="check-label"><input name="enabled" type="checkbox" ${timer.enabled?'checked':''}>Automatic start</label><div class="auto-start-interval"><label for="auto-start-interval">Every</label><input id="auto-start-interval" name="interval" type="number" min="1" step="1" required value="${h(timer.interval)}" aria-label="Repeat interval"><select name="unit" aria-label="Interval unit"><option value="minutes" ${timer.unit==='minutes'?'selected':''}>minutes</option><option value="hours" ${timer.unit==='hours'?'selected':''}>hours</option></select></div><p class="muted small">Runs while ApplyDesk is open. Stop turns the timer off.</p></form>`;
  const dialog=modal('Automatic start',body,'<button type="button" id="cancel-auto-start">Cancel</button><button type="submit" form="auto-start-form" class="primary" id="save-auto-start">Save</button>','auto-start-dialog');
  const form=dialog.querySelector('#auto-start-form');
  dialog.querySelector('#cancel-auto-start').onclick=()=>dialog.close();
  form.onsubmit=event=>{
    event.preventDefault();
    if(form.reportValidity())saveAutoStart(dialog,form);
  };
}

// Saving changes only the timer; activating it does not start or resume the agent immediately.
function saveAutoStart(dialog,form) {
  const data=new FormData(form),payload={enabled:data.has('enabled'),interval:Number(data.get('interval')),unit:data.get('unit')};
  const controls=[...form.querySelectorAll('input,select')];
  busy(dialog.querySelector('#save-auto-start'),async()=>{
    controls.forEach(control=>control.disabled=true);
    try{
      state.data.orchestrator.auto_start=await api('orchestrator/auto-start',payload);
      await refresh();updateAutoStart();
      if(dialog.querySelector('#auto-start-form')===form)dialog.close();
      toast(payload.enabled?`Automatic start set for every ${payload.interval} ${payload.unit}.`:'Automatic start is off.');
    }finally{controls.forEach(control=>control.disabled=false);}
  });
}

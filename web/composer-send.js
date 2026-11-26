import {state,api,refresh,icon,toast} from './ui.js';

let pending=false,resumePending=false;

// Work includes queued chat and tool execution between individual model calls.
export function composerIsWorking(engine) {
  return !engine.paused&&(!!engine.active||engine.messages.some(message=>
    message.role==='user'&&['queued','running'].includes(message.status)));
}

// One accessible icon represents Send, Stop, or cancellation still in progress.
export function updateComposeAction() {
  const engine=state.data.orchestrator,button=document.querySelector('#send-assistant');
  const stopping=engine.paused&&!!engine.active,working=composerIsWorking(engine);
  if(!engine.paused)resumePending=false;
  const label=stopping?'Stopping agent':working?'Stop agent':resumePending?'Resume queued message':'Send message';
  button.innerHTML=icon(working||stopping?'stop':'send');
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

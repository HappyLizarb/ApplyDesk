import {state,refresh,toast,api} from './ui.js';
import {renderApplications} from './applications.js';
import {renderProfile} from './profile.js';
import {renderDocuments} from './documents.js';
import {renderAgentSettings,updateAccount} from './agent-settings.js';
import {initializeAssistant,toggleAssistant} from './assistant.js';

const views={applications:renderApplications,profile:renderProfile,documents:renderDocuments,settings:renderAgentSettings};

// Navigation preserves browser history and avoids needless form redraws during polling.
function navigate() {
  const requested=location.hash.slice(1)||'applications';
  state.view=Object.hasOwn(views,requested)?requested:'applications';
  document.querySelectorAll('[data-view]').forEach(button=>{
    const active=button.dataset.view===state.view;
    button.classList.toggle('active',active);
    if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
  });
  document.querySelector('#main').dataset.view=state.view;
  views[state.view](document.querySelector('#main'));
  window.scrollTo(0,0);
  if(requested==='chat')toggleAssistant(true);
}

// Initial loading failures remain visible rather than leaving an empty app window.
async function start() {
  try {
    bindQuitControl();
    await refresh();initializeAssistant();navigate();
    document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>location.hash=button.dataset.view);
    window.addEventListener('hashchange',navigate);
    window.addEventListener('workspace-updated',()=>{
      if(state.view==='applications')renderApplications(document.querySelector('#main'));
      if(state.view==='settings')updateAccount();
    });
    setInterval(()=>refresh().catch(()=>{}),3500);
  } catch(error) {
    document.querySelector('#main').textContent='Could not open the local workspace. Restart ApplyDesk and try again.';
    toast(error.message,true);
  }
}
// The first click reveals confirmation; dismissing it never contacts the server.
function bindQuitControl() {
  const button=document.querySelector('#quit-app');
  button.onclick=()=>quitApp(button);
  document.addEventListener('click',event=>{
    if(!button.disabled&&!button.contains(event.target))setQuitExpanded(button,false);
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!button.disabled)setQuitExpanded(button,false);
  });
}

// Updating the text node preserves the existing close icon throughout confirmation.
function setQuitExpanded(button,expanded) {
  button.setAttribute('aria-expanded',String(expanded));
  button.querySelector('[data-quit-label]').textContent=expanded?'Quit ApplyDesk':'Quit';
  button.title=expanded?'Click again to quit ApplyDesk':'Quit';
  button.classList.toggle('quit-confirming',expanded);
}

// Only the confirmed second click shuts down, and failures remain ready for retry.
async function quitApp(button) {
  if(button.disabled)return;
  if(button.getAttribute('aria-expanded')!=='true'){
    setQuitExpanded(button,true);return;
  }
  button.disabled=true;button.setAttribute('aria-busy','true');
  try {
    await api('shutdown',{});
    window.close();
  } catch(error) {
    button.disabled=false;button.setAttribute('aria-busy','false');
    setQuitExpanded(button,true);
    toast(error.message,true);
  }
}

start();

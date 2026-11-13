import {task,busy} from './ui.js';

// This control restores the agent's existing Chrome window and never starts an application.
export function initializeApplicationWindow(panel) {
  const button=document.createElement('button');
  button.id='show-application';button.type='button';button.textContent='Show application';
  button.hidden=true;button.className='show-application';
  panel.querySelector('#application-run-status').after(button);
  button.onclick=()=>busy(button,()=>task('orchestrator/show-application',{}));
}

// Browser availability is observed from the running session, not inferred from model text.
export function updateApplicationWindow(panel,engine) {
  const button=panel.querySelector('#show-application');
  if(!button)return;
  button.hidden=!engine.application_browser?.available;
  button.setAttribute('aria-label','Show the current application in Chrome');
}

import {busy} from './ui.js';
import {dockApplicationWindow,restoreApplicationLayout} from './application-layout.js';

// This control restores the agent's existing Chrome window and never starts an application.
export function initializeApplicationWindow(panel) {
  const button=document.createElement('button');
  button.id='show-application';button.type='button';button.textContent='Show application workspace';
  button.hidden=true;button.className='show-application';
  panel.querySelector('#assistant-error').after(button);
  button.onclick=()=>busy(button,dockApplicationWindow);
}

// Browser availability is observed from the running session, not inferred from model text.
export function updateApplicationWindow(panel,engine) {
  const button=panel.querySelector('#show-application');
  if(!button)return;
  const browser=engine.application_browser;
  const wasVisible=!button.hidden;
  button.hidden=!browser?.available;
  button.setAttribute('aria-label','Show the application workspace beside ApplyDesk');
  // Polling can run between a focus check and a keystroke. Only the Show button moves windows.
  if(wasVisible&&button.hidden)restoreApplicationLayout();
}

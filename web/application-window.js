import {task} from './ui.js';

// Browser availability is observed from the running session, not inferred from model text.
export function updateApplicationWindow(panel,engine) {
  const button=panel.querySelector('#show-application');
  const available=Boolean(engine.application_browser?.available);
  if(!available&&!button.hidden)restoreApplicationLayout();
  button.hidden=!available;
}

let originalBounds=null,dockedBounds=null;

// Use the app's current display, including displays to the left of the main screen.
export function workspaceLayout(display) {
  const {availLeft=0,availTop=0,availWidth,availHeight}=display;
  const height=Math.max(320,Math.round(availHeight-24)),split=availWidth>=700;
  const width=Math.max(320,Math.floor((availWidth-24-(split?8:0))/(split?2:1)));
  const app={left:Math.round(availLeft+12),top:Math.round(availTop+12),width,height};
  const browser={...app,left:split?app.left+width+8:app.left};
  return {app,browser};
}

// The app moves itself; the service positions only its owned application browser.
export async function dockApplicationWindow() {
  const layout=workspaceLayout(window.screen);
  originalBounds??={left:window.screenX,top:window.screenY,width:window.outerWidth,height:window.outerHeight};
  window.resizeTo(layout.app.width,layout.app.height);
  window.moveTo(layout.app.left,layout.app.top);
  dockedBounds=layout.app;
  try {await task('orchestrator/show-application',{bounds:layout.browser});}
  catch(error) {restoreApplicationLayout();throw error;}
}

// Restore the earlier size only when the user has not since rearranged the app.
function restoreApplicationLayout() {
  const current={left:window.screenX,top:window.screenY,width:window.outerWidth,height:window.outerHeight};
  if(originalBounds&&dockedBounds&&Object.keys(current).every(key=>Math.abs(current[key]-dockedBounds[key])<=2)){
    window.resizeTo(originalBounds.width,originalBounds.height);
    window.moveTo(originalBounds.left,originalBounds.top);
  }
  originalBounds=null;dockedBounds=null;
}

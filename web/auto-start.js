import {state,h,icon,api,refresh,busy,modal,toast} from './ui.js';

// Poll the icon without replacing an open interval form or its unsaved edits.
export function updateAutoStart() {
  const button=document.querySelector('#auto-start-button');
  if(!button)return;
  const timer=state.data.orchestrator.auto_start;
  const label=timer?.enabled?`Automatic start every ${timer.interval} ${timer.unit}`:'Automatic start off';
  button.innerHTML=icon(timer?.enabled?'clock':'clock-off');
  button.setAttribute('aria-label',label);button.title=label;
  button.classList.toggle('active',!!timer?.enabled);
}

// A single timer resumes unfinished work; it never collects another task or prompt.
export function showAutoStart() {
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

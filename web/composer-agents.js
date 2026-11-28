import {state,h,api,refresh,toast} from './ui.js';

let saving=false,savingModel=false,renderedSettings='',pendingSelection=null;
const effortOrder=['default','none','minimal','low','medium','high','xhigh','max','ultra'];

// Native disclosure and range controls support keyboard use and outside dismissal.
export function initializeAgentMenu(panel) {
  const menu=panel.querySelector('#composer-agents');
  menu.addEventListener('change',event=>saveAgentSelection(event.target));
  menu.addEventListener('input',event=>previewEffort(event.target));
  menu.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();menu.open=false;menu.querySelector('summary').focus();}
  });
  document.addEventListener('click',event=>{if(!menu.contains(event.target))menu.open=false;});
}

// The agent keeps a model selector and one labeled slider with discrete supported stops.
function agentSlider(selection,models,levels,model) {
  const index=Math.max(0,levels.indexOf(selection.thinking)),effort=levels[index];
  const options=models.map(item=>`<option value="${h(item.id)}" ${item.id===selection.model?'selected':''}>${h(item.name)}</option>`).join('');
  const placeholder=model?'':`<option value="" disabled selected>${selection.model?h(selection.model)+' (unavailable)':'Choose a model'}</option>`;
  const dots=levels.map(level=>`<span class="agent-effort-dot" title="${h(level)}"></span>`).join('');
  return `<section class="agent-menu-row"><div class="agent-slider-heading"><h3>ApplyDesk Agent</h3><output class="agent-effort-label" for="master-thinking">${h(effort)}</output></div><label class="sr-only" for="master-model">ApplyDesk Agent model</label><select id="master-model" name="master.model">${placeholder}${options}</select><label class="sr-only" for="master-thinking">ApplyDesk Agent thinking strength</label><div class="agent-effort-track"><div class="agent-effort-fill" aria-hidden="true"></div><div class="agent-effort-dots" aria-hidden="true">${dots}</div><div class="agent-effort-thumb" aria-hidden="true"></div><input id="master-thinking" class="agent-thinking-slider" type="range" name="master.thinking" min="0" max="${levels.length-1}" step="1" value="${index}" aria-valuetext="${h(effort)}"></div></section>`;
}

// Dragging previews the effort locally; release or a keyboard change saves it once.
function previewEffort(input) {
  if(input.type!=='range')return;
  const track=input.closest('.agent-effort-track'),dots=track.querySelectorAll('.agent-effort-dot');
  const index=Number(input.value),value=dots[index].title;
  input.closest('.agent-menu-row').querySelector('output').textContent=value;
  input.setAttribute('aria-valuetext',value);
  const progress=dots.length>1?index/(dots.length-1):0;
  track.style.setProperty('--effort-position',`calc(${progress*100}% + ${16-32*progress}px)`);
  dots.forEach((dot,stop)=>{
    dot.classList.toggle('selected',stop===index);
    dot.classList.toggle('lit',stop<=index);
  });
  return value;
}

// Polls and saves keep the existing slider node so dragging and animation are uninterrupted.
export function updateAgentMenu(restoreThinking=false) {
  const engine=state.data.orchestrator,menu=document.querySelector('#composer-agents');
  if(!menu)return;
  const {account,settings}=engine,master=settings.roles.master;
  const model=account.models.find(item=>item.id===master.model);
  const levels=effortOrder.filter(value=>value==='default'||model?.thinking?.includes(value));
  menu.querySelector('#composer-model-label').textContent=model?.name||'Choose a model';
  menu.querySelector('#composer-thinking-label').textContent=master.thinking==='default'?'':master.thinking;
  const fingerprint=JSON.stringify([account.active,account.models,master.model]);
  const changed=!savingModel&&fingerprint!==renderedSettings;
  if(changed){
    const focused=menu.contains(document.activeElement)?document.activeElement.name:null;
    menu.querySelector('#composer-agent-rows').innerHTML=agentSlider(master,account.models,levels,model);
    if(focused)menu.querySelector(`[name="${focused}"]`)?.focus({preventScroll:true});
    renderedSettings=fingerprint;
  }
  const slider=menu.querySelector('#master-thinking'),disabled=savingModel||!account.connected||!!engine.active;
  menu.querySelector('#master-model').disabled=disabled||saving;
  slider.disabled=disabled||levels.length===1;
  if(changed||(!saving&&(restoreThinking||document.activeElement!==slider))){
    slider.value=Math.max(0,levels.indexOf(master.thinking));previewEffort(slider);
  }
  const status=menu.querySelector('#agent-menu-status');
  status.classList.toggle('sr-only',saving);
  status.textContent=saving?'Saving…':engine.active?'Stop the agent before changing its model.':account.connected?'':'Connect an account to choose a model.';
}

// Queue the latest choice while an earlier save completes, without dropping quick changes.
async function saveAgentSelection(control) {
  const engine=state.data.orchestrator;
  if(savingModel||engine.active||!engine.account.connected)return;
  const selection={...engine.settings.roles.master};
  if(control.id==='master-model'){
    selection.model=control.value;selection.thinking='default';savingModel=true;
  }else if(control.id==='master-thinking')selection.thinking=previewEffort(control);
  else return;
  pendingSelection={role:'master',selection};
  if(!saving)await flushSelection();
}

// Serial saves retain the latest choice without disabling the slider or rebuilding its rail.
async function flushSelection() {
  let restoreThinking=false;
  saving=true;updateAgentMenu();
  try {
    while(pendingSelection){
      const requested=pendingSelection;pendingSelection=null;
      await api('orchestrator/role-settings',requested);
      await refresh();
    }
  } catch(error){
    pendingSelection=null;toast(error.message,true);
    restoreThinking=true;
  } finally {
    saving=false;savingModel=false;updateAgentMenu(restoreThinking);
  }
}

import {state,h,icon,api,refresh,toast} from './ui.js';
import {roles} from './agent-settings.js';

let saving=false,savingModel=false,renderedSettings='',pendingSelection=null;
const effortOrder=['default','none','minimal','low','medium','high','xhigh','max','ultra'];

// Sliders step only through reasoning levels advertised for the selected model.
function effortLevels(model) {
  return effortOrder.filter(value=>value==='default'||model?.thinking?.includes(value));
}

// The compact picker exposes one saved model and thinking setting.
export function agentMenu() {
  return `<details id="composer-agents"><summary class="agent-menu-trigger" aria-label="Choose the agent model and thinking strength"><span id="composer-model-label"></span><span id="composer-thinking-label"></span>${icon('chevron-down')}</summary><div class="agent-menu-popover"><div id="composer-agent-rows"></div><p id="agent-menu-status" class="muted small" role="status"></p></div></details>`;
}

// Native disclosure and range controls support keyboard use and outside dismissal.
export function initializeAgentMenu(panel) {
  const menu=panel.querySelector('#composer-agents');
  menu.addEventListener('change',event=>saveAgentSelection(event.target));
  menu.addEventListener('input',event=>previewEffort(event.target));
  menu.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();menu.open=false;menu.querySelector('summary').focus();}
  });
  document.addEventListener('click',event=>{if(!menu.contains(event.target))menu.open=false;});
  updateAgentMenu();
}

// The agent keeps a model selector and one labeled slider with discrete supported stops.
function agentSlider(role,label,selection,models) {
  const model=models.find(item=>item.id===selection.model),levels=effortLevels(model);
  const index=Math.max(0,levels.indexOf(selection.thinking)),effort=levels[index];
  const options=models.map(item=>`<option value="${h(item.id)}" ${item.id===selection.model?'selected':''}>${h(item.name)}</option>`).join('');
  const placeholder=model?'':`<option value="" disabled selected>${selection.model?h(selection.model)+' (unavailable)':'Choose a model'}</option>`;
  const dots=levels.map((level,stop)=>`<span class="agent-effort-dot${stop===index?' selected':''}${stop<=index?' lit':''}" title="${h(level)}"></span>`).join('');
  return `<section class="agent-menu-row"><div class="agent-slider-heading"><h3>${h(label)}</h3><output class="agent-effort-label" for="${role}-thinking">${h(effort)}</output></div><label class="sr-only" for="${role}-model">${h(label)} model</label><select id="${role}-model" name="${role}.model">${placeholder}${options}</select><label class="sr-only" for="${role}-thinking">${h(label)} thinking strength</label><div class="agent-effort-track"><div class="agent-effort-fill" aria-hidden="true"></div><div class="agent-effort-dots" aria-hidden="true">${dots}</div><div class="agent-effort-thumb" aria-hidden="true"></div><input id="${role}-thinking" class="agent-thinking-slider" type="range" name="${role}.thinking" data-role="${role}" min="0" max="${levels.length-1}" step="1" value="${index}" aria-valuetext="${h(effort)}"></div></section>`;
}

// Dragging previews the effort locally; release or a keyboard change saves it once.
function previewEffort(input) {
  if(input.type!=='range')return;
  const engine=state.data.orchestrator,role=input.dataset.role;
  const model=engine.account.models.find(item=>item.id===engine.settings.roles[role].model);
  const value=effortLevels(model)[Number(input.value)];
  input.closest('.agent-menu-row').querySelector('output').textContent=value;
  input.setAttribute('aria-valuetext',value);
  const track=input.closest('.agent-effort-track');
  const progress=Number(input.max)?Number(input.value)/Number(input.max):0;
  track.style.setProperty('--effort-position',`calc(${progress*100}% + ${16-32*progress}px)`);
  track.querySelectorAll('.agent-effort-dot').forEach((dot,index)=>{
    dot.classList.toggle('selected',index===Number(input.value));
    dot.classList.toggle('lit',index<=Number(input.value));
  });
}

// Polls and saves keep the existing slider node so dragging and animation are uninterrupted.
export function updateAgentMenu() {
  const engine=state.data.orchestrator,menu=document.querySelector('#composer-agents');
  if(!menu)return;
  const {account,settings}=engine,master=settings.roles.master;
  const model=account.models.find(item=>item.id===master.model);
  menu.querySelector('#composer-model-label').textContent=model?.name||'Choose a model';
  menu.querySelector('#composer-thinking-label').textContent=master.thinking==='default'?'':master.thinking;
  const fingerprint=JSON.stringify([account.active,account.models,Object.values(settings.roles).map(role=>role.model)]);
  if(!savingModel&&fingerprint!==renderedSettings){
    const focused=menu.contains(document.activeElement)?document.activeElement.name:null;
    menu.querySelector('#composer-agent-rows').innerHTML=Object.entries(roles).map(([role,label])=>
      agentSlider(role,label,settings.roles[role],account.models)).join('');
    if(focused)menu.querySelector(`[name="${focused}"]`)?.focus({preventScroll:true});
    renderedSettings=fingerprint;
    menu.querySelectorAll('input[type=range]').forEach(previewEffort);
  }
  menu.querySelectorAll('select,input').forEach(control=>{
    control.disabled=savingModel||(saving&&control.tagName==='SELECT')||!account.connected||!!engine.active||(control.type==='range'&&control.max==='0');
    if(!saving&&control.type==='range'&&document.activeElement!==control){
      control.value=Math.max(0,effortLevels(model).indexOf(master.thinking));previewEffort(control);
    }
  });
  const status=menu.querySelector('#agent-menu-status');
  status.classList.toggle('sr-only',saving);
  status.textContent=saving?'Saving…':engine.active?'Stop the agent before changing its model.':account.connected?'':'Connect an account to choose a model.';
}

// Queue the latest choice while an earlier save completes, without dropping quick changes.
async function saveAgentSelection(control) {
  if(!['SELECT','INPUT'].includes(control.tagName)||savingModel)return;
  const engine=state.data.orchestrator,[role,field]=control.name.split('.');
  if(!roles[role]||engine.active||!engine.account.connected)return;
  const current=engine.settings.roles[role];
  const model=engine.account.models.find(item=>item.id===current.model);
  const value=field==='thinking'?effortLevels(model)[Number(control.value)]:control.value;
  const selected={...current,[field]:value};
  if(field==='model')selected.thinking='default';
  pendingSelection={role,selection:selected};
  savingModel=field==='model';
  if(!saving)await flushSelection();
}

// Serial saves retain the latest choice without disabling the slider or rebuilding its rail.
async function flushSelection() {
  saving=true;updateAgentMenu();
  try {
    while(pendingSelection){
      const requested=pendingSelection;pendingSelection=null;
      await api('orchestrator/role-settings',requested);
      await refresh();
    }
  } catch(error){
    pendingSelection=null;toast(error.message,true);
    const input=document.querySelector('#master-thinking'),engine=state.data.orchestrator;
    const model=engine.account.models.find(item=>item.id===engine.settings.roles.master.model);
    input.value=Math.max(0,effortLevels(model).indexOf(engine.settings.roles.master.thinking));
    previewEffort(input);
  } finally {
    saving=false;savingModel=false;updateAgentMenu();
  }
}

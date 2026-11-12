import {state,api,refresh,busy,toast} from './ui.js';
import {toggleAssistant} from './assistant.js';

const sources={linkedin:'LinkedIn',indeed:'Indeed',handshake:'Handshake'};
const supported=['linkedin','handshake'];
const active=new Set(['connecting','waiting_signin','scanning']);
const pending=new Set(),notified=new Set();
let reportsSeen=false;

// Enabled preferences alone never claim a verified platform connection.
function connection(platform) {
  const report=state.data.recommendations?.[platform]||{};
  const enabled=state.data.recommendation_preferences?.[platform]?.enabled===true;
  return {report,enabled,running:active.has(report.status),connected:enabled&&report.connected===true};
}

// One control connects or disconnects without collecting platform passwords.
export function jobPlatformsMarkup() {
  return `<section class="panel job-platforms" id="job-platforms" aria-labelledby="job-platforms-title"><h2 id="job-platforms-title" tabindex="-1">Job platforms</h2><div class="recommendation-sources">${Object.entries(sources).map(([platform,name])=>`<div class="recommendation-source"><h3>${name}</h3><button type="button" data-source-start="${platform}">Connect</button></div>`).join('')}</div></section>`;
}

// Poll controls in place so tracker updates preserve keyboard focus.
export function bindJobPlatforms(root) {
  root.querySelectorAll('[data-source-start]').forEach(button=>button.onclick=()=>changeSource(button.dataset.sourceStart));
  updateSources(root);
  if(state.focusJobPlatforms)focusPlatforms();
}

// Disconnect disables imports and cancels the run while preserving local sign-in.
async function changeSource(platform) {
  if(!supported.includes(platform)||pending.has(platform))return;
  const {connected}=connection(platform);
  pending.add(platform);updateSources(document.querySelector('#job-platforms'));
  try {
    if(connected)await api('recommendations/preference',{platform,enabled:false});
    else await api('recommendations/begin',{platform,limit:5,connect_only:true});
    await refresh();
  } catch(error) {toast(error.message,true);}
  finally {pending.delete(platform);updateSources(document.querySelector('#job-platforms'));}
}

function updateSources(root) {
  for(const button of root?.querySelectorAll('[data-source-start]')||[]) {
    const platform=button.dataset.sourceStart,unavailable=!supported.includes(platform);
    const {connected,running}=connection(platform),waiting=pending.has(platform)||(running&&!connected);
    button.textContent=connected?'Connected':'Connect';
    button.disabled=unavailable||waiting;
    button.setAttribute('aria-busy',String(waiting));
    button.setAttribute('aria-label',`${connected?'Disconnect':'Connect'} ${sources[platform]}`);
    button.title=unavailable?'Indeed connections are not available yet.':connected?'Disconnect; your local sign-in is kept.':`Connect ${sources[platform]}`;
  }
}

// Report a newly failed run once, without replaying historical failures on launch.
function notifyFailures() {
  const messages=[];
  for(const platform of supported) {
    const {report}=connection(platform),key=`${platform}:${report.run_id}`;
    if(!report.run_id||!['blocked','error','interrupted'].includes(report.status)||notified.has(key))continue;
    notified.add(key);
    if(reportsSeen)messages.push(`${sources[platform]}: ${report.message||'The connection could not import complete listings.'}`);
  }
  reportsSeen=true;
  if(messages.length)toast(messages.join(' · '),true);
}

// Find jobs shares chat's relevance gate; connecting alone never fills the tracker.
export async function findRecommendations(event) {
  const connected=supported.filter(platform=>connection(platform).connected);
  if(!connected.length)return openJobPlatforms();
  toggleAssistant(true);
  if(state.data.orchestrator.paused&&state.data.orchestrator.active)return toast('Wait for agents to stop before starting a search.');
  const industries=(state.data.profile.industries||[]).map(item=>item.name).filter(Boolean);
  const levels=state.data.profile.preferences?.levels||[];
  if(!industries.length){
    const input=document.querySelector('#assistant-input');
    if(!input.value.trim())input.value='Find jobs for ';
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.focus();return toast('Tell the agent which roles to find.');
  }
  const message=`Find jobs matching my selected industries: ${industries.join(', ')}${levels.length?`; job levels: ${levels.join(', ')}`:''}. Add only relevant matches.`;
  await busy(event?.currentTarget,async()=>{
    await api('orchestrator/send',{message});
    if(state.data.orchestrator.paused)await api('orchestrator/control',{paused:false});
    await refresh();
  });
}

// Navigation leads to the connection controls in Profile.
export function openJobPlatforms() {
  state.focusJobPlatforms=true;
  if(state.view==='profile')focusPlatforms();else location.hash='profile';
}

function focusPlatforms() {
  requestAnimationFrame(()=>{
    const heading=document.querySelector('#job-platforms-title');
    heading?.focus();heading?.scrollIntoView({block:'start'});state.focusJobPlatforms=false;
  });
}

window.addEventListener('workspace-updated',()=>{updateSources(document.querySelector('#job-platforms'));notifyFailures();});

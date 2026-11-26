import {state,api,refresh,busy,toast} from './ui.js';
import {toggleAssistant} from './assistant.js';

// Find jobs starts public Google research directly, without a job-platform connection.
export async function findRecommendations(event) {
  toggleAssistant(true);
  if(state.data.orchestrator.paused&&state.data.orchestrator.active)return toast('Wait for the agent to stop before starting a search.');
  const roles=(state.data.profile.industries||[]).filter(item=>item.name);
  if(!roles.length){
    const input=document.querySelector('#assistant-input');
    if(!input.value.trim())input.value='Find jobs for ';
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.focus();return toast('Tell the agent which roles to find.');
  }
  const preferences=roles.map(row=>`${row.name}${String(row.years??'')?` (${row.years} years of experience)`:''}${(row.levels??state.data.profile.preferences?.levels)?.length?`; seeking ${(row.levels??state.data.profile.preferences.levels).join(', ')}`:''}`).join(' | ');
  const message=`Use Google X-ray search to find public employer and careers listings matching these role preferences: ${preferences}. Treat each row's levels as desired roles, not qualifications. Read complete listings and add only relevant matches.`;
  await busy(event?.currentTarget,async()=>{
    await api('orchestrator/send',{message});
    if(state.data.orchestrator.paused)await api('orchestrator/control',{paused:false});
    await refresh();
  });
}

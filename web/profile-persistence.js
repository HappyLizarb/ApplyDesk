import {state,api} from './ui.js';

// An older running server must never receive fields it would silently discard.
export async function persistProfile(payload,sections) {
  if(!Number.isInteger(state.data.profile_schema)||state.data.profile_schema<2){
    throw new Error('ApplyDesk needs a full restart to save these fields. Your edits are still on this page; keep a copy before quitting.');
  }
  const saved=await api('profile',payload);
  const complete=saved&&saved.confirmed===payload.confirmed&&Array.isArray(saved.skills)&&
    saved.identity&&typeof saved.identity==='object'&&!Array.isArray(saved.identity)&&
    saved.preferences&&typeof saved.preferences==='object'&&!Array.isArray(saved.preferences);
  if(!complete||!Number.isInteger(saved.revision)||saved.revision<=payload.revision){
    throw new Error('The profile save could not be verified. Your edits are still on this page.');
  }
  for(const [section,[,fields]] of Object.entries(sections)){
    const expected=payload[section],actual=saved[section];
    const retained=Array.isArray(actual)&&actual.length===expected.length&&expected.every((row,index)=>
      fields.every(key=>actual[index]?.[key]===row[key]));
    if(!retained)throw new Error('ApplyDesk did not retain every profile field. Your edits are still on this page. Restart ApplyDesk before retrying.');
  }
  if(saved.revision>=(state.data.profile?.revision??0))state.data.profile=saved;
  return saved;
}

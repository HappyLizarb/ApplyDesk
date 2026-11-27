import {state,h,icon,modal} from './ui.js';
import {composeInstruction} from './assistant.js';

// Keep the last search visible without mistaking discovered listings for qualified jobs.
export function discoverySummary() {
  const report=state.data.discovery;
  if(!report?.criteria)return '';
  const filters=[report.criteria.keywords,report.criteria.location,report.criteria.remote?'Remote':''].filter(Boolean).join(' · ');
  const outcome=report.status==='searching'?'Searching…':report.status==='error'?'Search interrupted':`${report.added_count} added`;
  return `<div class="search-summary"><p>${h(filters)} <span aria-hidden="true">—</span> ${h(outcome)}</p><button class="quiet" id="search-report">Search details ${icon('arrow')}</button></div>`;
}

// Source links and failures remain inspectable after the browser task has ended.
export function showSearchReport() {
  const report=state.data.discovery;
  if(!report?.criteria)return;
  const warnings=[...new Set(report.warnings||[])];
  const candidates=(report.candidates||[]).map(job=>`<li><a href="${h(job.url)}" target="_blank" rel="noreferrer">${h(job.title)}</a> · ${h(job.company)}${job.location?` · ${h(job.location)}`:''}</li>`).join('');
  const sources=(report.sources||[]).map(source=>`<li><a href="${h(source.url)}" target="_blank" rel="noreferrer">${h(source.label||source.url)}</a></li>`).join('');
  modal('Search details',`<div class="modal-body"><h3>${h(report.criteria.keywords)}</h3><p class="muted small">${report.status==='searching'?'Search in progress':`${h(report.added_count)} added to your applications`}. Qualifications have not been checked.</p>${candidates?`<ul class="discovery-report">${candidates}</ul>`:''}${warnings.map(message=>`<p class="notice warning">${h(message)}</p>`).join('')}${sources?`<section class="detail-section"><h3>Sources</h3><ul class="discovery-report">${sources}</ul></section>`:''}</div>`);
}

// Describe the saved role preferences in a draft the user can edit and send.
export function findJobs() {
  const roles=(state.data.profile.industries||[]).filter(role=>role.name);
  const preferences=roles.map(role=>{
    const levels=role.levels??state.data.profile.preferences?.levels??[];
    return [role.name,levels.join(', ')].filter(Boolean).join(' · ');
  });
  composeInstruction(preferences.length
    ? 'Find jobs matching my saved role preferences: '+preferences.join(' | ')+'.'
    : 'Find jobs for ');
}

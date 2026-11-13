import {state,h,icon,labels,api,refresh,task,busy,toast,modal,taskStrip} from './ui.js';
import {openEditor} from './editor.js';
import {requestChat,openChatTask} from './chat.js';
import {findJobs,discoverySummary,showSearchReport} from './discovery.js';
import {compensationLabel,descriptionItems} from './job-details.js';

const selectedJobs=new Set();
let removingJobs=false;

// The primary surface is a spreadsheet; no fabricated sample jobs are inserted.
export function renderApplications(root) {
  const focusedJob=root.contains(document.activeElement)?document.activeElement.dataset.selectJob:null;
  const search=root.querySelector('#search-jobs');
  const selection=document.activeElement===search?[search.selectionStart,search.selectionEnd]:null;
  const jobs=state.data.jobs;
  for(const id of selectedJobs)if(!jobs.some(job=>job.id===id))selectedJobs.delete(id);
  const shown=jobs.filter(job=>matchesStatus(job,state.filter) && `${job.company} ${job.title}`.toLowerCase().includes(state.query.toLowerCase()));
  const filters=[['all','All'],['draft','Drafts'],['needs_review','Needs input'],['submitted','Submitted'],['skipped','Skipped']];
  root.innerHTML=`<header class="page-head"><h1>Applications</h1><div class="actions"><button id="remove-jobs" class="danger" hidden></button><button id="add-job">${icon('plus')}Add link</button></div></header>${taskStrip()}${discoverySummary()}
    <section class="applications-panel"><div class="toolbar"><div class="tabs" role="group" aria-label="Application status filter">${filters.map(([key,label])=>`<button data-filter="${key}" class="${state.filter===key?'active':''}" aria-pressed="${state.filter===key}">${label}<span class="tab-count">${jobs.filter(job=>matchesStatus(job,key)).length}</span></button>`).join('')}</div><div class="search-wrap">${icon('search')}<input class="search" id="search-jobs" placeholder="Search applications" aria-label="Search applications" value="${h(state.query)}"></div></div>
    <div class="table-wrap"><table aria-label="Applications"><thead><tr><th scope="col">Company</th><th scope="col">Role</th><th scope="col">Compensation</th><th scope="col">Resume</th><th scope="col">Cover letter</th><th scope="col">Account</th><th scope="col">Status</th><th scope="col" class="job-selection"><span class="sr-only">Select applications</span></th></tr></thead><tbody>${shown.map(jobRow).join('')}</tbody></table>${!shown.length?emptyState(jobs.length):''}</div>
    <div class="table-footer"><span>${shown.length} of ${jobs.length} applications</span><span>/ = cover letter not required</span></div></section>`;
  bindSelection(root);
  root.querySelector('#add-job').onclick=addJob;
  root.querySelector('[data-find-first]')?.addEventListener('click',findJobs);
  root.querySelector('#search-report')?.addEventListener('click',showSearchReport);
  root.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{state.filter=button.dataset.filter;renderApplications(root);});
  root.querySelector('#search-jobs').oninput=event=>{state.query=event.target.value;renderApplications(root);};
  bindRows(root);
  if(focusedJob)root.querySelector(`[data-select-job="${CSS.escape(focusedJob)}"]`)?.focus({preventScroll:true});
  if(selection) {const input=root.querySelector('#search-jobs');input.focus();input.setSelectionRange(...selection);}
}

// The needs-input filter includes documents changed after an earlier review.
function matchesStatus(job,filter) {
  return filter==='all'||job.status===filter||(filter==='needs_review'&&['documents_changed','submission_unknown'].includes(job.status));
}

// Each resume and letter cell names a concrete immutable document version.
function jobRow(job) {
  const docCell=(id,label)=>id?`<button class="doc-link" data-doc="${h(id)}" data-job="${h(job.id)}">${icon('document')}${label}</button>`:'<span class="muted">—</span>';
  const cover=job.cover_id?docCell(job.cover_id,'Edit letter'):job.cover_state==='not_required'?'<span title="Checked: not required">/</span>':'<span class="muted small">Not checked</span>';
  return `<tr><td><div class="company-cell" title="${h(job.company)}">${h(job.company||'—')}</div></td><td><button class="job-title" data-detail="${h(job.id)}" title="${h(job.title)}">${h(job.title)}</button>${job.analysis?.category?`<div class="company">${h(job.analysis.category)}</div>`:''}</td><td><div class="compensation-cell">${h(compensationLabel(job.compensation))||'<span class="muted">Not stated</span>'}</div></td><td>${docCell(job.resume_id,'Resume')}</td><td>${cover}</td><td><button class="doc-link account-link" data-account="${h(job.id)}" title="${h(job.account)}">${h(job.account||'Add account')}</button></td><td><span class="badge ${h(job.status)}"><span class="status-dot"></span>${h(labels[job.status]||job.status)}</span></td><td class="job-selection"><input type="checkbox" data-select-job="${h(job.id)}" aria-label="Select ${h(job.title)} at ${h(job.company||'unknown company')}" ${selectedJobs.has(job.id)?'checked':''} ${removingJobs?'disabled':''}></td></tr>`;
}

// Give a clear first action without pretending any applications already exist.
function emptyState(hasJobs) {
  return `<div class="empty"><div class="empty-symbol">${icon(hasJobs?'search':'applications')}</div><h2>${hasJobs?'No matching applications':'Find your next role'}</h2>${hasJobs?'<p>Try another status or search term.</p>':`<button data-find-first>${icon('search')}Find jobs</button>`}</div>`;
}

// Row controls keep document editing and employer credentials within the workspace.
function bindRows(root) {
  root.querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>jobDetails(button.dataset.detail));
  root.querySelectorAll('[data-account]').forEach(button=>button.onclick=()=>accountDialog(button.dataset.account));
  root.querySelectorAll('[data-doc]').forEach(button=>button.onclick=()=>openEditor(button.dataset.doc,button.dataset.job));
}

// Selections survive filtering and polling; removal always uses explicit record IDs.
function bindSelection(root) {
  const button=root.querySelector('#remove-jobs');
  button.hidden=!selectedJobs.size;
  button.textContent=removingJobs?'Removing…':`Remove selected (${selectedJobs.size})`;
  button.disabled=removingJobs;
  button.onclick=()=>removeSelected(root);
  root.querySelectorAll('[data-select-job]').forEach(input=>input.onchange=()=>{
    if(input.checked)selectedJobs.add(input.dataset.selectJob);else selectedJobs.delete(input.dataset.selectJob);
    bindSelection(root);
  });
}

// Keep failed selections available for retry and refresh the tracker after success.
async function removeSelected(root) {
  if(removingJobs||!selectedJobs.size)return;
  const ids=[...selectedJobs];
  removingJobs=true;renderApplications(root);
  try {
    const result=await api('jobs/remove',{ids});
    ids.forEach(id=>selectedJobs.delete(id));
    await refresh();
    toast(`${result.removed} application${result.removed===1?'':'s'} removed.`);
  } catch(error) {toast(error.message,true);}
  finally {removingJobs=false;if(state.view==='applications')renderApplications(root);}
}

// Adding a URL records intent but does not visit the employer or run paid agents.
function addJob() {
  const dialog=modal('Add application',`<form><div class="modal-body"><label>Job listing URL<input name="url" type="url" placeholder="https://company.com/careers/role" required autofocus></label><p class="field-help">Saves the link for a chat draft.</p></div><div class="modal-foot"><button class="primary">Add application</button></div></form>`);
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const job=await api('job/add',{url:event.target.url.value});await refresh();dialog.close();jobDetails(job.id);});};
}

// Parsed source paragraphs become bullets; all source content remains escaped.
function descriptionSections(job) {
  const fields=[['responsibilities','Responsibilities'],['about','About the team'],['required_qualifications','Minimum / required qualifications'],['preferred_qualifications','Preferred qualifications'],['why_join_us','Why join us']];
  return fields.map(([key,label])=>{
    const items=descriptionItems(job.detail_sections?.[key]);
    const content=items.length?`<ul class="job-details job-detail-list">${items.map(item=>`<li>${h(item)}</li>`).join('')}</ul>`:'<p class="job-details muted">Not separately stated</p>';
    return `<section class="detail-section" aria-labelledby="detail-${key}"><h3 id="detail-${key}">${label}</h3>${content}</section>`;
  }).join('');
}

// Details distinguish skipped, ready, manual, and submitted states explicitly.
export function jobDetails(id) {
  const job=state.data.jobs.find(item=>item.id===id);
  if(!job)return;
  const issues=[...new Set([...(job.exceptions||[]),...(job.recommendation_source?.issues||[])])].map(issue=>`<div class="notice warning">${h(issue)}</div>`).join('');
  const body=`<div class="modal-body"><div class="actions"><span class="badge ${h(job.status)}"><span class="status-dot"></span>${h(labels[job.status])}</span><a href="${h(job.url)}" target="_blank" rel="noreferrer">Job description ↗</a></div>${issues}<p class="muted small">${h(job.progress||'Not prepared yet. No employer form has been changed.')}</p><div class="details-grid"><div><h3>Company</h3>${h(job.company||'Not assessed')}</div><div><h3>Documents</h3>${job.resume_id?`<button class="inline-link" data-doc="${h(job.resume_id)}" data-job="${h(id)}">View resume</button>`:'No resume selected'}<br>${job.cover_id?`<button class="inline-link" data-doc="${h(job.cover_id)}" data-job="${h(id)}">Edit cover letter</button>`:h(job.cover_state==='not_required'?'/ · not required':'Cover letter: not checked')}</div><div class="wide"><h3>Compensation</h3><div class="job-details" data-compensation>${h(compensationLabel(job.compensation))||'<span class="muted">Not stated</span>'}</div></div></div>${descriptionSections(job)}<section class="detail-section"><h3>Activity</h3><ol class="timeline">${job.history.slice(-8).map(item=>`<li>${h(item.message)}</li>`).join('')||'<li>No preparation activity yet.</li>'}</ol></section><p class="muted small">Unattended submissions require a chat request with a limit and target locations.</p></div>`;
  const footer=`<button id="prepare-job" class="primary" ${job.status==='submitted'?'disabled':''}>Prepare draft in chat</button>`;
  const dialog=modal(job.title,body,footer,'job-detail-dialog');
  bindRows(dialog);
  dialog.querySelector('#prepare-job').onclick=event=>busy(event.target,async()=>{
    const queued=await requestChat('application',id);dialog.close();await openChatTask(queued.id);
  });
}

// One confirmed employer account is shared across roles, with exact sign-in boundaries.
function accountDialog(id) {
  const job=state.data.jobs.find(item=>item.id===id);
  const destination=job.live_url||job.recommendation_source?.employer_url||(!job.recommendation_source?job.url:'');
  const origin=job.account_origin||(destination?new URL(destination).origin:'');
  const dialog=modal('Employer account',`<form><div class="modal-body"><label>Account email or username<input name="account" value="${h(job.account||state.data.profile.identity.email)}" autocomplete="off" required></label><label class="detail-section">Exact sign-in website origin<input name="origin" type="url" value="${h(origin)}" required></label><p class="field-help">Example: https://careers.company.com. Credentials will not be reused on a different origin.</p><label class="detail-section">Password<input name="password" type="password" autocomplete="new-password" placeholder="${job.has_password?'Saved in Keychain · leave blank to keep':'Enter the existing account password'}"></label><p class="muted small">Use this existing account for all roles at ${h(job.company||'this employer')}. Passwords stay in macOS Keychain.</p></div><div class="modal-foot"><button class="primary">Save for this employer</button></div></form>`);
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const form=event.target;await api('job/account',{id,account:form.account.value,account_origin:form.origin.value,password:form.password.value});form.password.value='';await refresh();dialog.close();toast('Account saved.');});};
}

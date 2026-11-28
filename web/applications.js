import {state,h,icon,labels,api,refresh,busy,toast,modal,taskStrip} from './ui.js';
import {openEditor} from './documents.js';
import {composeInstruction} from './assistant.js';
import {compensationLabel,descriptionItems} from './job-details.js';

const selectedJobs=new Set();
let removingJobs=false;
const manualStatuses=['new','draft','needs_review','submitted','skipped'];

// The primary surface is a spreadsheet; no fabricated sample jobs are inserted.
export function renderApplications(root) {
  const focusedJob=root.contains(document.activeElement)?document.activeElement.dataset.selectJob:null;
  const focusedAll=document.activeElement===root.querySelector('#select-all-jobs');
  const search=root.querySelector('#search-jobs');
  const selection=document.activeElement===search?[search.selectionStart,search.selectionEnd]:null;
  const jobs=state.data.jobs;
  if(selectedJobs.size){
    const jobIds=new Set(jobs.map(job=>job.id));
    for(const id of selectedJobs)if(!jobIds.has(id))selectedJobs.delete(id);
  }
  const grouped=groupJobs(jobs);
  const documents=availableDocuments();
  const query=state.query.toLowerCase();
  const shown=(grouped[state.filter]||[]).filter(job=> `${job.company} ${job.title}`.toLowerCase().includes(query));
  const filters=[['all','All'],['draft','Drafts'],['needs_review','Needs input'],['submitted','Submitted'],['skipped','Skipped']];
  root.innerHTML=`<header class="page-head"><h1>Applications</h1><div class="actions"><button id="remove-jobs" class="danger" hidden></button><button id="add-job">${icon('plus')}Add link</button></div></header>${taskStrip()}${discoverySummary()}
    <section class="applications-panel"><div class="toolbar"><div class="tabs" role="group" aria-label="Application status filter">${filters.map(([key,label])=>`<button data-filter="${key}" class="${state.filter===key?'active':''}" aria-pressed="${state.filter===key}">${label}<span class="tab-count">${grouped[key]?.length||0}</span></button>`).join('')}</div><div class="search-wrap">${icon('search')}<input class="search" id="search-jobs" placeholder="Search applications" aria-label="Search applications" value="${h(state.query)}"></div></div>
    <div class="table-wrap"><table aria-label="Applications"><thead><tr>${['Company','Role','Compensation','Resume','Cover letter','Account','Status'].map(name=>`<th scope="col">${name}</th>`).join('')}<th scope="col" class="job-selection"><input id="select-all-jobs" type="checkbox" aria-label="Select/unselect all shown applications" title="Select/unselect all shown applications"></th></tr></thead><tbody>${shown.map(job=>jobRow(job,documents)).join('')}</tbody></table>${!shown.length?emptyState(jobs.length):''}</div>
    <div class="table-footer"><span>${shown.length} of ${jobs.length} applications</span><span>/ = cover letter not required</span></div></section>`;
  bindSelection(root);
  root.querySelector('#add-job').onclick=addJob;
  root.querySelector('[data-find-first]')?.addEventListener('click',findJobs);
  root.querySelector('#search-report')?.addEventListener('click',showSearchReport);
  root.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{state.filter=button.dataset.filter;renderApplications(root);});
  root.querySelector('#search-jobs').oninput=event=>{state.query=event.target.value;renderApplications(root);};
  bindRows(root);
  if(focusedJob)root.querySelector(`[data-select-job="${CSS.escape(focusedJob)}"]`)?.focus({preventScroll:true});
  if(focusedAll)root.querySelector('#select-all-jobs').focus({preventScroll:true});
  if(selection) {const input=root.querySelector('#search-jobs');input.focus();input.setSelectionRange(...selection);}
}

// Count each board tab once, treating a changed document or an uncertain submission as needing input.
function groupJobs(jobs) {
  const groups=Object.create(null);
  for(const job of jobs){
    const status=job.manual_status||job.status;
    const tab=['documents_changed','submission_unknown'].includes(status)?'needs_review':status;
    (groups[tab]??=[]).push(job);
  }
  groups.all=jobs;
  return groups;
}

// Keep transient engine states visible while offering only the five user-editable board states.
function statusControl(job) {
  const status=job.manual_status||job.status;
  const automatic=!manualStatuses.includes(status);
  const options=automatic?[status,...manualStatuses]:manualStatuses;
  const editable=!['preparing','submitting'].includes(job.status);
  const title=job.manual_status?'Manually marked '+labels[status]:'Change application status';
  return `<label class="badge status-picker ${h(status)}" title="${h(title)}"><span class="status-dot" aria-hidden="true"></span>
    <select data-job-status="${h(job.id)}" data-previous="${h(status)}" aria-label="Status for ${h(job.title)} at ${h(job.company)}" ${editable?'':'disabled'}>
    ${options.map(key=>`<option value="${h(key)}" ${key===status?'selected':''} ${automatic&&key===status?'disabled':''}>${h(labels[key]||key)}</option>`).join('')}</select></label>`;
}

// A retired resume can still appear on earlier applications.
function availableDocuments() {
  const {documents=[],resumes=[]}=state.data;
  return new Set([...documents,...resumes].map(record=>record.id));
}

// An employer upload is historical evidence even after the current resume selection changes.
function applicationResume(job,label,css,documents) {
  const ledger=Array.isArray(job.attachments)?job.attachments:[];
  const receipt=ledger.findLast(item=>item?.kind==='resume'&&item.document_id);
  const id=receipt?.document_id||job.resume_id;
  if(!id)return css==='doc-link'?'<span class="muted">—</span>':'No resume selected';
  if(receipt&&!documents.has(id))return `<span class="muted">${h(receipt.filename||'Resume')} · removed</span>`;
  return `<button class="${css}" data-doc="${h(id)}">${css==='doc-link'?icon('document'):''}${label}</button>`;
}

// Each resume and letter cell names a concrete immutable document version.
function jobRow(job,documents) {
  const cover=job.cover_id?`<button class="doc-link" data-doc="${h(job.cover_id)}">${icon('document')}View letter</button>`:job.cover_state==='not_required'?'<span title="Checked: not required">/</span>':`<span class="muted small">${job.cover_state==='required'?'Manual handling':'Not checked'}</span>`;
  return `<tr><td><div class="company-cell" title="${h(job.company)}">${h(job.company||'—')}</div></td><td><button class="job-title" data-detail="${h(job.id)}" title="${h(job.title)}">${h(job.title)}</button>${job.analysis?.category?`<div class="company">${h(job.analysis.category)}</div>`:''}</td><td><div class="compensation-cell">${h(compensationLabel(job.compensation))||'<span class="muted">Not stated</span>'}</div></td><td>${applicationResume(job,'Resume','doc-link',documents)}</td><td>${cover}</td><td><button class="doc-link account-link" data-account="${h(job.id)}" title="${h(job.account)}">${h(job.account||'Add account')}</button></td><td>${statusControl(job)}</td><td class="job-selection"><input type="checkbox" data-select-job="${h(job.id)}" aria-label="Select ${h(job.title)} at ${h(job.company||'unknown company')}" ${selectedJobs.has(job.id)?'checked':''} ${removingJobs?'disabled':''}></td></tr>`;
}

// Give a clear first action without pretending any applications already exist.
function emptyState(hasJobs) {
  return `<div class="empty"><div class="empty-symbol">${icon(hasJobs?'search':'applications')}</div><h2>${hasJobs?'No matching applications':'Find your next role'}</h2>${hasJobs?'<p>Try another status or search term.</p>':`<button data-find-first>${icon('search')}Find jobs</button>`}</div>`;
}

// Row controls keep document previews and employer credentials within the workspace.
function bindRows(root) {
  root.querySelectorAll('[data-job-status]').forEach(select=>select.onchange=()=>saveBoardStatus(select));
  root.querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>jobDetails(button.dataset.detail));
  root.querySelectorAll('[data-account]').forEach(button=>button.onclick=()=>accountDialog(button.dataset.account));
  root.querySelectorAll('[data-doc]').forEach(button=>button.onclick=()=>openEditor(button.dataset.doc));
}

// A manual edit updates the tracker and filter counts through the same persisted request.
function saveBoardStatus(select) {
  const {jobStatus:id,previous}=select.dataset;
  return busy(select,async()=>{
    try {
      await api('job/status',{id,previous,status:select.value});
      await refresh();
      if(state.view==='applications')renderApplications(document.querySelector('#main'));
      if(select.closest('dialog')?.open)jobDetails(id);
    } catch(error){select.value=previous;throw error;}
  });
}

// Selections survive filtering and polling; removal always uses explicit record IDs.
function bindSelection(root) {
  const button=root.querySelector('#remove-jobs');
  const rows=[...root.querySelectorAll('[data-select-job]')];
  const all=root.querySelector('#select-all-jobs');
  const updateControls=()=>{
    const count=rows.filter(input=>input.checked).length;
    all.checked=rows.length>0&&count===rows.length;
    all.indeterminate=count>0&&count<rows.length;
    button.hidden=!selectedJobs.size;
    button.textContent=removingJobs?'Removing…':`Remove selected (${selectedJobs.size})`;
  };
  updateControls();
  all.disabled=removingJobs||!rows.length;
  button.disabled=removingJobs;
  button.onclick=()=>removeSelected(root);
  root.querySelector('table').onchange=({target})=>{
    if(target!==all&&!target.hasAttribute('data-select-job'))return;
    for(const input of target===all?rows:[target]){
      input.checked=target.checked;
      if(input.checked)selectedJobs.add(input.dataset.selectJob);else selectedJobs.delete(input.dataset.selectJob);
    }
    updateControls();
  };
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

// Adding a URL records intent but does not visit the employer or run the agent.
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
function jobDetails(id) {
  const job=state.data.jobs.find(item=>item.id===id);
  if(!job)return;
  const issues=[...new Set([...(job.exceptions||[]),...(job.recommendation_source?.issues||[])])].map(issue=>`<div class="notice warning">${h(issue)}</div>`).join('');
  const body=`<div class="modal-body"><div class="actions">${statusControl(job)}<a href="${h(job.url)}" target="_blank" rel="noreferrer">Job description ↗</a></div>${issues}<p class="muted small">${h(job.progress||'Not prepared yet. No employer form has been changed.')}</p><div class="details-grid"><div><h3>Company</h3>${h(job.company||'Not assessed')}</div><div><h3>Documents</h3>${applicationResume(job,'View resume','inline-link',availableDocuments())}<br>${job.cover_id?`<button class="inline-link" data-doc="${h(job.cover_id)}">View saved letter</button>`:h(job.cover_state==='not_required'?'/ · not required':job.cover_state==='required'?'Cover letter required · continue manually':'Cover letter: not checked')}</div><div class="wide"><h3>Compensation</h3><div class="job-details" data-compensation>${h(compensationLabel(job.compensation))||'<span class="muted">Not stated</span>'}</div></div></div>${descriptionSections(job)}<section class="detail-section"><h3>Activity</h3><ol class="timeline">${job.history.slice(-8).map(item=>`<li>${h(item.message)}</li>`).join('')||'<li>No preparation activity yet.</li>'}</ol></section><p class="muted small">Ask in chat to submit applications; application count and target locations are optional.</p></div>`;
  const draft=(state.data.chat_tasks||[]).find(task=>task.kind==='application'&&task.target_id===id);
  const footer=`${draft?'<button id="view-draft">View draft</button>':''}<button id="prepare-job" class="primary" ${job.status==='submitted'||['submitted','skipped'].includes(job.manual_status)?'disabled':''}>Prepare draft in chat</button>`;
  const dialog=modal(job.title,body,footer,'job-detail-dialog');
  bindRows(dialog);
  dialog.querySelector('#view-draft')?.addEventListener('click',()=>{dialog.close();openChatTask(draft.id);});
  dialog.querySelector('#prepare-job').onclick=()=>{
    dialog.close();
    composeInstruction(`Prepare a draft for ${job.title||'this job'} (${job.url}).`);
  };
}

// One confirmed employer account is shared across roles, with exact sign-in boundaries.
function accountDialog(id) {
  const job=state.data.jobs.find(item=>item.id===id);
  const destination=job.live_url||job.recommendation_source?.employer_url||(!job.recommendation_source?job.url:'');
  const origin=job.account_origin||(destination?new URL(destination).origin:'');
  const dialog=modal('Employer account',`<form><div class="modal-body"><label>Account email or username<input name="account" value="${h(job.account||state.data.profile.identity.email)}" autocomplete="off" required></label><label class="detail-section">Exact sign-in website origin<input name="origin" type="url" value="${h(origin)}" required></label><p class="field-help">Example: https://careers.company.com. Credentials will not be reused on a different origin.</p><label class="detail-section">Password<input name="password" type="password" autocomplete="new-password" placeholder="${job.has_password?'Saved in Keychain · leave blank to keep':'Enter the existing account password'}"></label><p class="muted small">Use this existing account for all roles at ${h(job.company||'this employer')}. Passwords stay in macOS Keychain.</p></div><div class="modal-foot"><button class="primary">Save for this employer</button></div></form>`);
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const form=event.target;await api('job/account',{id,account:form.account.value,account_origin:form.origin.value,password:form.password.value});form.password.value='';await refresh();dialog.close();toast('Account saved.');});};
}

// Show draft content and local issues without implying submission approval.
async function openChatTask(id) {
  try {
    const context=await api('chat/task?id='+encodeURIComponent(id)),task=context.task;
    const statuses={queued:'Queued for chat',draft_ready:'Draft prepared',needs_input:'Needs input',stale:'Inputs changed'};
    const issues=task.issues||[];
    const documents=context.review.documents.map(doc=>`<button data-chat-document="${h(doc.id)}">${h(doc.title||doc.filename)}</button>`).join('');
    const body=`<div class="modal-body"><span class="badge">${h(statuses[task.status])}</span><p class="muted small">Prepared by your agent. Check the draft before using it.</p>${issues.map(issue=>`<p class="notice warning">${h(issue)}</p>`).join('')}${draftContents(task)}${documents?`<h3>Documents</h3><div class="actions">${documents}</div>`:''}${['needs_input','stale'].includes(task.status)?'<button id=retry-chat-task>Retry task</button>':''}</div>`;
    const dialog=modal(task.title,body);
    dialog.querySelector('#retry-chat-task')?.addEventListener('click',()=>{dialog.close();composeInstruction('Retry the draft task '+id+'.');});
    dialog.querySelectorAll('[data-chat-document]').forEach(button=>button.onclick=()=>{dialog.close();openEditor(button.dataset.chatDocument);});
  } catch(error){toast(error.message,true);}
}

// The task record shows evidence and proposed answers without presenting approval.
function draftContents(task) {
  const result=task.result;
  if(!result)return `<p>${task.instruction?h(task.instruction):'Waiting for the agent to prepare a draft.'}</p>`;
  const requirements=result.analysis?.required||[];
  const answers=result.answers||[];
  return `${requirements.length?`<h3>Required qualifications</h3><ul>${requirements.map(item=>`<li>${h(item.text)}</li>`).join('')}</ul>`:''}${answers.length?`<h3>Proposed answers</h3>${answers.map(item=>`<p><strong>${h(item.question)}</strong><br>${h(item.answer)}</p>`).join('')}`:''}${(result.notes||[]).map(note=>`<p>${h(note)}</p>`).join('')}`;
}

// Keep the last search visible without mistaking discovered listings for qualified jobs.
function discoverySummary() {
  const report=state.data.discovery;
  if(!report?.criteria)return '';
  const filters=[report.criteria.keywords,report.criteria.location,report.criteria.remote?'Remote':''].filter(Boolean).join(' · ');
  const outcome=report.status==='searching'?'Searching…':report.status==='error'?'Search interrupted':`${report.added_count} added`;
  return `<div class="search-summary"><p>${h(filters)} <span aria-hidden="true">—</span> ${h(outcome)}</p><button class="quiet" id="search-report">Search details ${icon('arrow')}</button></div>`;
}

// Source links and failures remain inspectable after the browser task has ended.
function showSearchReport() {
  const report=state.data.discovery;
  if(!report?.criteria)return;
  const warnings=[...new Set(report.warnings||[])];
  const candidates=(report.candidates||[]).map(job=>`<li><a href="${h(job.url)}" target="_blank" rel="noreferrer">${h(job.title)}</a> · ${h(job.company)}${job.location?` · ${h(job.location)}`:''}</li>`).join('');
  const sources=(report.sources||[]).map(source=>`<li><a href="${h(source.url)}" target="_blank" rel="noreferrer">${h(source.label||source.url)}</a></li>`).join('');
  modal('Search details',`<div class="modal-body"><h3>${h(report.criteria.keywords)}</h3><p class="muted small">${report.status==='searching'?'Search in progress':`${h(report.added_count)} added to your applications`}. Qualifications have not been checked.</p>${candidates?`<ul class="discovery-report">${candidates}</ul>`:''}${warnings.map(message=>`<p class="notice warning">${h(message)}</p>`).join('')}${sources?`<section class="detail-section"><h3>Sources</h3><ul class="discovery-report">${sources}</ul></section>`:''}</div>`);
}

// Describe the saved role preferences in a draft the user can edit and send.
function findJobs() {
  const roles=(state.data.profile.industries||[]).filter(role=>role.name);
  const preferences=roles.map(role=>{
    const levels=role.levels??state.data.profile.preferences?.levels??[];
    return [role.name,levels.join(', ')].filter(Boolean).join(' · ');
  });
  composeInstruction(preferences.length
    ? 'Find jobs matching my saved role preferences: '+preferences.join(' | ')+'.'
    : 'Find jobs for ');
}

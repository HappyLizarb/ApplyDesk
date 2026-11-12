import {state,h,api,refresh,busy,toast,modal} from './ui.js';
import {openEditor} from './editor.js';

const statuses={queued:'Queued for chat',awaiting_review:'Reviews pending',reviewed_draft:'Reviewed draft',needs_input:'Needs input',stale:'Inputs changed'};
const roles={document_reviewer:'Document Reviewer',application_auditor:'Application Auditor'};

// Queue requests locally; only a user-authorized chat can execute the next step.
export async function requestChat(kind,target_id,instruction='') {
  const task=await api('chat/enqueue',{kind,target_id,instruction});
  await refresh();toast('Draft queued.');
  return task;
}

// Draft contents are escaped and reviews are explicitly described as chat reports.
export async function openChatTask(id) {
  try {
    const context=await api('chat/task?id='+encodeURIComponent(id)),task=context.task;
    const issues=[...(task.issues||[]),...Object.values(task.reviews).flatMap(review=>review.issues)];
    const verified=Object.values(task.reviews).length&&Object.values(task.reviews).every(review=>review.identity_verified);
    const reviews=Object.entries(task.reviews).map(([role,review])=>`<div class="file-row"><strong>${h(roles[role])}</strong><span>${review.passed?'Reported pass':'Issues found'}</span></div>`).join('');
    const documents=context.review.documents.map(doc=>`<button data-chat-document="${h(doc.id)}">${h(doc.title||doc.filename)}</button>`).join('');
    const body=`<div class="modal-body"><span class="badge">${h(statuses[task.status])}</span><p class="muted small">Unfinished draft. ${verified?'Reviews came from separate app-controlled agent runs.':'External reviewer identities are self-reported.'}</p>${issues.map(issue=>`<p class="notice warning">${h(issue)}</p>`).join('')}${draftContents(task)}${documents?`<h3>Documents</h3><div class="actions">${documents}</div>`:''}<h3 class="detail-section">Chat reviews</h3>${reviews||'<p class="muted">No reviews recorded.</p>'}${['needs_input','stale'].includes(task.status)?'<button id=retry-chat-task>Retry task</button>':''}${task.kind==='profile'&&task.result?'<a href="#profile" id="review-profile-link">Review extracted facts in Profile</a>':''}</div>`;
    const dialog=modal(task.title,body);
    dialog.querySelector('#retry-chat-task')?.addEventListener('click',event=>busy(event.target,async()=>{await api('orchestrator/retry',{task_id:id});await refresh();dialog.close();}));
    dialog.querySelectorAll('[data-chat-document]').forEach(button=>button.onclick=()=>{dialog.close();openEditor(button.dataset.chatDocument);});
    dialog.querySelector('#review-profile-link')?.addEventListener('click',()=>dialog.close());
  } catch(error){toast(error.message,true);}
}

// The task record shows evidence and proposed answers without presenting approval.
function draftContents(task) {
  const result=task.result;
  if(!result)return `<p>${task.instruction?h(task.instruction):'Waiting for the Application Agent to prepare a draft.'}</p>`;
  const requirements=result.analysis?.required||[];
  const answers=result.answers||[];
  return `${requirements.length?`<h3>Required qualifications</h3><ul>${requirements.map(item=>`<li>${h(item.text)}</li>`).join('')}</ul>`:''}${answers.length?`<h3>Proposed answers</h3>${answers.map(item=>`<p><strong>${h(item.question)}</strong><br>${h(item.answer)}</p>`).join('')}`:''}${(result.notes||[]).map(note=>`<p>${h(note)}</p>`).join('')}`;
}

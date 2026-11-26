import {state,h,api,refresh,busy,toast,modal} from './ui.js';
import {openEditor} from './editor.js';

const statuses={queued:'Queued for chat',draft_ready:'Draft prepared',needs_input:'Needs input',stale:'Inputs changed'};

// Queue requests locally; only a user-authorized chat can execute the next step.
export async function requestChat(kind,target_id,instruction='') {
  const task=await api('chat/enqueue',{kind,target_id,instruction});
  await refresh();toast('Draft queued.');
  return task;
}

// Show draft content and local issues without implying submission approval.
export async function openChatTask(id) {
  try {
    const context=await api('chat/task?id='+encodeURIComponent(id)),task=context.task;
    const issues=task.issues||[];
    const documents=context.review.documents.map(doc=>`<button data-chat-document="${h(doc.id)}">${h(doc.title||doc.filename)}</button>`).join('');
    const body=`<div class="modal-body"><span class="badge">${h(statuses[task.status])}</span><p class="muted small">Prepared by your agent. Check the draft before using it.</p>${issues.map(issue=>`<p class="notice warning">${h(issue)}</p>`).join('')}${draftContents(task)}${documents?`<h3>Documents</h3><div class="actions">${documents}</div>`:''}${['needs_input','stale'].includes(task.status)?'<button id=retry-chat-task>Retry task</button>':''}${task.kind==='profile'&&task.result?'<a href="#profile" id="review-profile-link">Review extracted facts in Profile</a>':''}</div>`;
    const dialog=modal(task.title,body);
    dialog.querySelector('#retry-chat-task')?.addEventListener('click',event=>busy(event.target,async()=>{await api('orchestrator/retry',{task_id:id});await refresh();dialog.close();}));
    dialog.querySelectorAll('[data-chat-document]').forEach(button=>button.onclick=()=>{dialog.close();openEditor(button.dataset.chatDocument);});
    dialog.querySelector('#review-profile-link')?.addEventListener('click',()=>dialog.close());
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

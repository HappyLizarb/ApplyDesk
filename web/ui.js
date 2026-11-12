// Shared UI state contains no API keys or employer passwords.
export const state = {data:null,view:'applications',filter:'all',query:''};
export const h = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const asset = path => '/asset/' + path.split('/').map(encodeURIComponent).join('/');
// Shared symbols keep navigation and table controls visually consistent without a library.
export const icon = name => `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
export const labels = {draft:'Draft',new:'Not started',preparing:'Preparing',needs_review:'Needs input',documents_changed:'Document changed',ready:'Ready to review',manual_review:'In browser',submitting:'Submitting',submission_unknown:'Check submission',submitted:'Submitted',skipped:'Skipped'};
// Only completed refreshes supersede older requests; a pending poll cannot hide useful state.
let refreshStarted=0,refreshApplied=0;

// The local server requires both its HttpOnly session and an exact request origin.
export async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-ApplyDesk':'1'},body:JSON.stringify(data)};
  const response = await fetch('/api/' + path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The action failed.');
  return value;
}

// Refresh state without replacing a form the user may currently be editing.
export async function refresh() {
  const request=++refreshStarted,next=await api('state');
  if(request<refreshApplied)return state.data;
  if(!next||typeof next!=='object'||Array.isArray(next)||!Array.isArray(next.jobs)||
    !next.profile||typeof next.profile!=='object'||Array.isArray(next.profile)||
    typeof next.profile.confirmed!=='boolean'||!Number.isSafeInteger(next.profile.revision??0)||
    (next.profile.revision??0)<0)throw new Error('The workspace returned an invalid state. Your current view has been kept.');
  const previous=JSON.stringify(state.data),currentProfile=state.data?.profile;
  if((currentProfile?.revision??0)>(next.profile.revision??0))next.profile=currentProfile;
  state.data=next;refreshApplied=request;
  document.querySelector('#job-count').textContent = state.data.jobs.length;
  document.querySelector('#profile-indicator').classList.toggle('confirmed',state.data.profile.confirmed);
  if(previous!==JSON.stringify(state.data))window.dispatchEvent(new Event('workspace-updated'));
  return state.data;
}

// Show operation failures in a persistent live region instead of silently dismissing them.
export function toast(message, error=false) {
  const element = document.querySelector('#toast');
  element.textContent = message; element.hidden = false;
  element.classList.toggle('danger',error);
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.hidden = true, error ? 18000 : 6000);
}

// Long-running browser and model calls expose one task that can be observed safely.
export async function task(path,data) {
  const {task:id} = await api(path,data);
  while (true) {
    await new Promise(resolve=>setTimeout(resolve,700));
    await refresh();
    const current = state.data.tasks.find(item=>item.id===id);
    if (current?.status === 'error') throw new Error(current.message);
    if (current?.status === 'done') return current.result;
  }
}

// Disable controls during async actions without replacing dropdown options or values.
export async function busy(button, action) {
  const original = button?.textContent, replaceLabel = button?.tagName === 'BUTTON';
  if (button) {button.disabled=true; if(replaceLabel)button.textContent='Working…';}
  try {return await action();}
  catch(error) {toast(error.message,true); return null;}
  finally {if(button) {button.disabled=false;if(replaceLabel)button.textContent=original;}}
}

// Modal content is built only from escaped user strings and static markup.
export function modal(title, body, footer='', className='') {
  const dialog=document.querySelector('#modal');
  if (dialog.open) dialog.close();
  dialog.oncancel=null;
  dialog.className=className;
  dialog.setAttribute('aria-labelledby','modal-title');
  dialog.innerHTML=`<div class="modal-head"><h2 id="modal-title">${h(title)}</h2><button class="close" aria-label="Close dialog">×</button></div>${body}${footer ? `<div class="modal-foot">${footer}</div>`:''}`;
  dialog.querySelector('.close').onclick=()=>dialog.close();
  dialog.showModal();
  return dialog;
}

// File bytes stay on this Mac until the user explicitly starts an agent task.
export async function upload(file,kind) {
  if (!file || file.size>20*1024*1024) throw new Error('Choose a DOCX or PDF file smaller than 20 MB.');
  const data=await new Promise((resolve,reject)=>{
    const reader=new FileReader(); reader.onerror=reject;
    reader.onload=()=>resolve(reader.result.split(',')[1]); reader.readAsDataURL(file);
  });
  return task('import',{filename:file.name,kind,data});
}

// One small status strip summarizes currently running work.
export function taskStrip() {
  const current=state.data?.tasks.find(item=>item.status==='running');
  if(!current)return '';
  const preparing=state.data.jobs.find(job=>job.status==='preparing');
  const message=current.id==='discover-jobs'?'Finding and checking public listings…':preparing?.progress||'Agent task in progress…';
  return `<div class="task-strip" role="status"><span class="spinner"></span>${h(message)}</div>`;
}

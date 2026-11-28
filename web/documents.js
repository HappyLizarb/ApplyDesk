import {state,h,icon,asset,api,refresh,busy,toast,modal,task,upload,taskStrip} from './ui.js';

// Uploaded resumes remain unchanged; their tags select the active application attachment.
export function renderDocuments(root) {
  const {resumes=[],uploads=[],profile:{transcript_id:transcriptId}}=state.data;
  const activeIds=new Set(resumes.map(record=>record.id));
  const other=uploads.filter(record=>!activeIds.has(record.id)&&record.id!==transcriptId);
  const redraw=()=>{if(root.isConnected&&state.view==='documents')renderDocuments(root);};
  const saved=async message=>{await refresh();redraw();toast(message);};
  root.innerHTML=`<header class="page-head"><h1>Documents</h1></header>${taskStrip()}
    <div class="documents-stack"><section class="panel"><div class="panel-heading"><h2>Resumes</h2><button class="primary" id="upload-resume">Choose resume</button></div>
    ${resumes.length?resumes.map(record=>fileRow(record,true)).join(''):`<div class="empty"><div class="empty-symbol">${icon('document')}</div><h3>No resumes yet</h3></div>`}</section>
    <section class="panel"><div class="panel-heading"><h2>Transcript</h2></div><label>Transcript · optional<input type="file" id="transcript-upload" accept=".pdf,.docx"></label><p id="transcript-status" data-transcript-id="${h(transcriptId||'')}" class="field-help">${transcriptId?'Saved locally.':'No transcript uploaded.'}</p>${transcriptId?`<button class="quiet danger" data-delete-document="${h(transcriptId)}">Delete transcript</button>`:''}</section>${other.length?`<section class="panel"><div class="panel-heading"><h2>Other uploads</h2></div>${other.map(record=>fileRow(record,false)).join('')}</section>`:''}</div>`;
  root.querySelector('#upload-resume').onclick=event=>busy(event.currentTarget,async()=>{
    if((await task('choose-resume',{}))?.selected)await saved('Resume selected. Choose a tag.');
  });
  root.querySelectorAll('[data-resume-tag]').forEach(select=>select.onchange=()=>tagResume(select,saved));
  root.querySelector('#transcript-upload').onchange=event=>importTranscript(event.target,saved);
  showTranscript(root,transcriptId);
  root.querySelectorAll('[data-document]').forEach(button=>button.onclick=()=>openEditor(button.dataset.document));
  root.querySelectorAll('[data-delete-document]').forEach(button=>button.onclick=()=>deleteDocument(button.dataset.deleteDocument,redraw));
}

// Transcript imports use the existing server-owned profile attachment, without a profile save.
function importTranscript(input,saved) {
  if(!input.files[0])return;
  return busy(input,async()=>{
    try {await upload(input.files[0],'transcript');await saved('Transcript saved locally.');}
    finally {input.value='';}
  });
}

// Resolve saved transcript links through their document records, never guessed paths.
async function showTranscript(root,identity) {
  if(!identity)return;
  try {
    const document=await api('document?id='+encodeURIComponent(identity));
    const target=root.querySelector('#transcript-status');
    if(target?.dataset.transcriptId===identity)target.innerHTML=`<a href="${asset(document.source_path)}" target="_blank" rel="noopener">${h(document.filename)}</a>`;
  } catch(error){toast(error.message,true);}
}

// Active resumes have a tag selector; older uploads share the same preview and deletion controls.
function fileRow(record,activeResume) {
  const id=h(record.id),name=h(record.filename||record.title),category=record.category;
  const categories=state.data.resume_categories||['General'];
  const orphan=category&&!categories.includes(category);
  const tag=activeResume?`<select data-resume-tag="${id}" aria-label="Tag for ${name}"><option value="" disabled ${category?'':'selected'}>Choose tag</option>${orphan?`<option selected disabled value="${h(category)}">${h(category)} · not in Profile</option>`:''}${categories.map(option=>`<option value="${h(option)}" ${option===category?'selected':''}>${h(option)}</option>`).join('')}</select>`:'';
  const kinds={profile:'Profile',transcript:'Transcript',resume:'Resume',cover_template:'Saved template',resume_template:'Saved template'};
  return `<div class="file-row${activeResume?' resume-row':''}">
    ${activeResume?`<span class="file-icon" aria-hidden="true">${icon('document')}</span>`:''}
    <div class="file-info"><strong>${name}</strong>${activeResume?'':`<span>${h(kinds[record.kind]||'Document')}</span>`}</div>
    ${tag}<div class="actions"><button data-document="${id}">Preview</button><button class="quiet danger" data-delete-document="${id}" aria-label="Delete ${name}">Delete</button></div></div>`;
}

// A tag change replaces that category's active row while retaining historical attachments.
function tagResume(select,saved) {
  const id=select.dataset.resumeTag;
  const previous=state.data.resumes.find(item=>item.id===id)?.category||'';
  return busy(select,async()=>{
    try {await api('resume/tag',{document_id:id,category:select.value});await saved('Resume tag saved.');}
    catch(error){select.value=previous;throw error;}
  });
}

// Saved documents remain available for preview and download without customization tools.
export async function openEditor(id) {
  const pending=modal('Document preview','<p class="modal-body" role="status">Loading pages…</p>','','editor-dialog');
  const loading=pending.querySelector('[role=status]');
  let document;
  try {document=await api('document?id='+encodeURIComponent(id)+'&preview=1');}
  catch(error){
    if(pending.open&&loading.isConnected){pending.close();toast(error.message,true);}
    return;
  }
  if(!pending.open||!loading.isConnected)return;
  const uploaded=document.kind!=='document',title=document.title||document.filename;
  const pages=(document.image_paths||[]).map((path,index)=>
    `<img class="document-page" src="${asset(path)}" alt="Page ${index+1} of ${h(title)}" loading="${index?'lazy':'eager'}">`).join('');
  const body=`<div class="modal-body document-preview" aria-label="Document preview">${pages}<p class="preview-status" role="status" ${pages?'hidden':''}>Page preview unavailable. Download the file to view it.</p></div>`;
  const files=uploaded?[[document.source_path,document.filename,'Download original']]:[
    [document.pdf_path,title+'.pdf','Download PDF'],[document.docx_path,title+'.docx','Download DOCX']];
  const footer=files.filter(([path])=>path).map(([path,name,label])=>`<a href="${asset(path)}" download="${h(name)}">${label}</a>`).join(' · ');
  const dialog=modal(title,body,footer+'<button class="quiet danger" id="delete-preview-file">Delete file</button>','editor-dialog');
  for(const page of dialog.querySelectorAll('.document-page')){
    const unavailable=()=>{page.hidden=true;dialog.querySelector('.preview-status').hidden=false;};
    page.onerror=unavailable;
    if(page.complete&&!page.naturalWidth)unavailable();
  }
  dialog.querySelector('#delete-preview-file').onclick=()=>deleteDocument(id,()=>window.dispatchEvent(new HashChangeEvent('hashchange')));
}

// A shared explicit action removes the selected file while preserving application history.
export async function deleteDocument(identity,afterDelete) {
  let record=[...(state.data.uploads||[]),...(state.data.resumes||[]),...(state.data.profile_imports||[]),...(state.data.documents||[])].find(item=>item.id===identity);
  try {record??=await api('document?id='+encodeURIComponent(identity));}
  catch(error){toast(error.message,true);return;}
  const dialog=modal('Delete file',`<div class="modal-body"><p>Delete <strong>${h(record.filename||record.title)}</strong>?</p><p class="muted small">${record.kind==='resume'?'ApplyDesk will remove the resume from its library and delete its private previews.':'The stored file and previews will be removed.'} Work using the file will stop. Application history and saved profile facts will stay; affected drafts will need input.</p></div>`,
    '<button id="delete-document-cancel">Cancel</button><button id="delete-document-confirm" class="danger">Delete</button>');
  dialog.querySelector('#delete-document-cancel').onclick=()=>dialog.close();
  dialog.querySelector('#delete-document-confirm').onclick=event=>busy(event.target,async()=>{
    const result=await api('document/delete',{document_id:identity});
    await refresh();dialog.close();afterDelete?.();
    toast(result.cleanup_pending?'File removed. Disk cleanup will finish when ApplyDesk restarts.':'File deleted.');
  });
}

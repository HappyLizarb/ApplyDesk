import {state,h,icon,asset,api,refresh,busy,toast,modal,upload,taskStrip} from './ui.js';

// Uploaded resumes remain unchanged; their tags select the active application attachment.
export function renderDocuments(root) {
  const {resumes=[]}=state.data;
  const other=(state.data.uploads||[]).filter(item=>!resumes.some(row=>row.id===item.id)&&item.id!==state.data.profile.transcript_id);
  root.innerHTML=`<header class="page-head"><h1>Documents</h1></header>${taskStrip()}
    <div class="documents-stack"><section class="panel"><div class="panel-heading"><h2>Resumes</h2><button class="primary" id="upload-resume">Upload</button></div><input type="file" id="resume-upload" accept=".pdf,.docx" hidden>
    ${resumes.length?resumes.map(documentRow).join(''):`<div class="empty"><div class="empty-symbol">${icon('document')}</div><h3>No resumes yet</h3></div>`}</section>
    <section class="panel"><div class="panel-heading"><h2>Transcript</h2></div><label>Transcript · optional<input type="file" id="transcript-upload" accept=".pdf,.docx"></label><p id="transcript-status" data-transcript-id="${h(state.data.profile.transcript_id||'')}" class="field-help">${state.data.profile.transcript_id?'Saved locally.':'No transcript uploaded.'}</p>${state.data.profile.transcript_id?`<button class="quiet danger" data-delete-document="${h(state.data.profile.transcript_id)}">Delete transcript</button>`:''}</section>${other.length?`<section class="panel"><div class="panel-heading"><h2>Other uploads</h2></div>${other.map(uploadRow).join('')}</section>`:''}</div>`;
  root.querySelector('#upload-resume').onclick=()=>root.querySelector('#resume-upload').click();
  root.querySelector('#resume-upload').onchange=event=>importResume(event.target,root);
  root.querySelectorAll('[data-resume-tag]').forEach(select=>select.onchange=()=>tagResume(select,root));
  root.querySelector('#transcript-upload').onchange=event=>importTranscript(event.target,root);
  showTranscript(root,state.data.profile.transcript_id);
  root.querySelectorAll('[data-document]').forEach(button=>button.onclick=()=>openEditor(button.dataset.document));
  root.querySelectorAll('[data-delete-document]').forEach(button=>button.onclick=()=>deleteDocument(button.dataset.deleteDocument,()=>{if(root.isConnected&&state.view==='documents')renderDocuments(root);}));
}

// Transcript imports use the existing server-owned profile attachment, without a profile save.
async function importTranscript(input,root) {
  if(!input.files[0])return;
  input.disabled=true;
  try {
    await upload(input.files[0],'transcript');await refresh();
    if(root.isConnected&&state.view==='documents')renderDocuments(root);
    toast('Transcript saved locally.');
  } catch(error){toast(error.message,true);}
  finally {input.disabled=false;input.value='';}
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

// Show each original file with one explicit industry or General tag.
function documentRow(document) {
  const categories=state.data.resume_categories||['General'];
  const orphan=document.category&&!categories.includes(document.category);
  return `<div class="file-row resume-row"><span class="file-icon" aria-hidden="true">${icon('document')}</span><div class="file-info"><strong>${h(document.filename)}</strong></div><select data-resume-tag="${h(document.id)}" aria-label="Tag for ${h(document.filename)}"><option value="" disabled ${document.category?'':'selected'}>Choose tag</option>${orphan?`<option selected disabled value="${h(document.category)}">${h(document.category)} · not in Profile</option>`:''}${categories.map(category=>`<option value="${h(category)}" ${category===document.category?'selected':''}>${h(category)}</option>`).join('')}</select><div class="actions"><button data-document="${h(document.id)}">Preview</button><button class="quiet danger" data-delete-document="${h(document.id)}" aria-label="Delete ${h(document.filename)}">Delete</button></div></div>`;
}

// Upload locally without generation, then let the user choose which tag to replace.
async function importResume(input,root) {
  if(!input.files[0])return;
  const button=root.querySelector('#upload-resume');
  await busy(button,async()=>{
    await upload(input.files[0],'resume');await refresh();
    if(root.isConnected&&state.view==='documents')renderDocuments(root);
    toast('Resume uploaded. Choose a tag.');
  });
  input.value='';
}

// A tag change replaces that category's active row while retaining historical attachments.
async function tagResume(select,root) {
  const document=state.data.resumes.find(item=>item.id===select.dataset.resumeTag);
  select.disabled=true;
  try {
    await api('resume/tag',{document_id:document.id,category:select.value});await refresh();
    if(root.isConnected&&state.view==='documents')renderDocuments(root);
    toast('Resume tag saved.');
  } catch(error){select.value=document.category||'';toast(error.message,true);}
  finally {select.disabled=false;}
}

// Older and profile uploads remain reachable even when they are no longer selected.
function uploadRow(record) {
  const names={profile:'Profile',transcript:'Transcript',resume:'Resume',cover_template:'Saved template',resume_template:'Saved template'};
  return `<div class="file-row"><div class="file-info"><strong>${h(record.filename||record.title)}</strong><span>${h(names[record.kind]||'Document')}</span></div><div class="actions"><button data-document="${h(record.id)}">Preview</button><button class="quiet danger" data-delete-document="${h(record.id)}" aria-label="Delete ${h(record.filename||record.title)}">Delete</button></div></div>`;
}

// Saved documents remain available for preview and download without customization tools.
export async function openEditor(id) {
  let document;
  try {document=await api('document?id='+encodeURIComponent(id));}catch(error){return toast(error.message,true);}
  const uploaded=document.kind!=='document',title=document.title||document.filename;
  const body=`<div class="modal-body template-single"><iframe class="editor-preview" title="${uploaded?'Uploaded resume preview':'Saved document preview'}" src="${asset(document.pdf_path)}"></iframe></div>`;
  const files=uploaded?[[document.source_path,document.filename,'Download original']]:[
    [document.pdf_path,title+'.pdf','Download PDF'],[document.docx_path,title+'.docx','Download DOCX']];
  const footer=files.filter(([path])=>path).map(([path,name,label])=>`<a href="${asset(path)}" download="${h(name)}">${label}</a>`).join(' · ');
  const dialog=modal(title,body,footer+'<button class="quiet danger" id="delete-preview-file">Delete file</button>','editor-dialog');
  dialog.querySelector('#delete-preview-file').onclick=()=>deleteDocument(id,()=>window.dispatchEvent(new HashChangeEvent('hashchange')));
}

// A shared explicit action removes the selected file while preserving application history.
export async function deleteDocument(identity,afterDelete) {
  let record=[...(state.data.uploads||[]),...(state.data.resumes||[]),...(state.data.profile_imports||[]),...(state.data.documents||[])].find(item=>item.id===identity);
  try {record??=await api('document?id='+encodeURIComponent(identity));}
  catch(error){toast(error.message,true);return;}
  const dialog=modal('Delete file',`<div class="modal-body"><p>Delete <strong>${h(record.filename||record.title)}</strong>?</p><p class="muted small">The stored file and previews will be removed. Work using the file will stop. Application history and saved profile facts will stay; affected drafts will need input.</p></div>`,
    '<button id="delete-document-cancel">Cancel</button><button id="delete-document-confirm" class="danger">Delete</button>');
  dialog.querySelector('#delete-document-cancel').onclick=()=>dialog.close();
  dialog.querySelector('#delete-document-confirm').onclick=event=>busy(event.target,async()=>{
    const result=await api('document/delete',{document_id:identity});
    await refresh();dialog.close();afterDelete?.();
    toast(result.cleanup_pending?'File removed. Disk cleanup will finish when ApplyDesk restarts.':'File deleted.');
  });
}

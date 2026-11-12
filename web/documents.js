import {state,h,icon,asset,api,refresh,busy,toast,modal,upload,taskStrip} from './ui.js';
import {openEditor} from './editor.js';

// Uploaded resumes remain unchanged; their tags select the active application attachment.
export function renderDocuments(root) {
  const {templates,resumes=[]}=state.data;
  const covers=templates.filter(item=>item.kind==='cover_template');
  root.innerHTML=`<header class="page-head"><h1>Documents</h1></header>${taskStrip()}
    <div class="documents-stack"><section class="panel"><div class="panel-heading"><h2>Resumes</h2><button class="primary" id="upload-resume">Upload</button></div><input type="file" id="resume-upload" accept=".pdf,.docx" hidden>
    ${resumes.length?resumes.map(documentRow).join(''):`<div class="empty"><div class="empty-symbol">${icon('document')}</div><h3>No resumes yet</h3></div>`}</section>
    <section class="panel"><div class="panel-heading"><h2>Cover letter template</h2><button id="import-cover-template">Import template</button></div>${covers.map(templateRow).join('')}<form id="cover-template-form" class="template-choice"><label>Default template<select name="cover_template_id"><option value="">None selected</option>${covers.map(item=>`<option value="${h(item.id)}" ${item.id===state.data.settings.cover_template_id?'selected':''} ${item.confirmed?'':'disabled'}>${h(item.filename)}${item.confirmed?'':' · confirm layout first'}</option>`).join('')}</select></label><button type="submit" class="primary">Save template</button></form></section>
    <section class="panel"><div class="panel-heading"><h2>Transcript</h2></div><label>Transcript · optional<input type="file" id="transcript-upload" accept=".pdf,.docx"></label><p id="transcript-status" data-transcript-id="${h(state.data.profile.transcript_id||'')}" class="field-help">${state.data.profile.transcript_id?'Saved locally.':'No transcript uploaded.'}</p></section></div>
    <p class="muted small">PDF templates need a layout check before use. For complex layouts, use a DOCX source.</p>`;
  root.querySelector('#cover-template-form').onsubmit=saveCoverTemplate;
  root.querySelector('#upload-resume').onclick=()=>root.querySelector('#resume-upload').click();
  root.querySelector('#resume-upload').onchange=event=>importResume(event.target,root);
  root.querySelectorAll('[data-resume-tag]').forEach(select=>select.onchange=()=>tagResume(select,root));
  root.querySelector('#import-cover-template').onclick=importTemplate;
  root.querySelector('#transcript-upload').onchange=event=>importTranscript(event.target,root);
  showTranscript(root,state.data.profile.transcript_id);
  root.querySelectorAll('[data-document]').forEach(button=>button.onclick=()=>openEditor(button.dataset.document));
  root.querySelectorAll('[data-template]').forEach(button=>button.onclick=()=>previewTemplate(button.dataset.template));
  root.querySelectorAll('[data-delete-document]').forEach(button=>button.onclick=()=>deleteDocument(button.dataset.deleteDocument,root));
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

// Saving a document preference preserves the separately configured models and credentials.
function saveCoverTemplate(event) {
  event.preventDefault();
  const template=event.target.elements.cover_template_id.value;
  busy(event.submitter,async()=>{
    await api('cover-template',{cover_template_id:template});
    await refresh();toast('Cover letter template saved.');
  });
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

// Cover templates remain separate from generated letter versions.
function templateRow(template) {
  return `<div class="file-row resume-row"><span class="file-icon" aria-hidden="true">${icon('document')}</span><div class="file-info"><strong>${h(template.filename)}</strong><span>Cover letter template · ${template.confirmed?'Ready':'Needs comparison'}</span></div><div class="actions"><button data-template="${h(template.id)}">${template.confirmed?'Preview':'Compare layout'}</button><button class="quiet danger" data-delete-document="${h(template.id)}" aria-label="Delete ${h(template.filename)}">Delete</button></div></div>`;
}

// Removing a library upload preserves files already referenced by existing applications.
function deleteDocument(identity,root) {
  const record=[...(state.data.resumes||[]),...(state.data.templates||[])].find(item=>item.id===identity);
  if(!record)return;
  const dialog=modal('Delete file',`<div class="modal-body"><p>Delete <strong>${h(record.filename)}</strong> from Documents?</p><p class="muted small">Files already used by applications will stay available in those applications.</p></div>`,
    '<button id="delete-document-cancel">Cancel</button><button id="delete-document-confirm" class="danger">Delete</button>');
  dialog.querySelector('#delete-document-cancel').onclick=()=>dialog.close();
  dialog.querySelector('#delete-document-confirm').onclick=event=>busy(event.target,async()=>{
    const result=await api('document/delete',{document_id:identity});await refresh();dialog.close();
    if(root.isConnected&&state.view==='documents')renderDocuments(root);
    toast(result.cleanup_pending?'Removed from Documents. File cleanup will retry when ApplyDesk restarts.':'File deleted from Documents.');
  });
}

// Import and rendering happen locally; generation requires a separate explicit action.
function importTemplate() {
  const dialog=modal('Import template',`<form id="template-form"><div class="modal-body"><label class="detail-section">File · DOCX or PDF<input type="file" name="file" accept=".docx,.pdf" required></label><p class="muted small">DOCX preserves layout best. Import stays local; agent tasks send content to your provider.</p></div><div class="modal-foot"><button class="primary" type="submit">Import template</button></div></form>`);
  dialog.querySelector('form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{
    const form=event.target; const result=await upload(form.file.files[0],'cover_template');
    await refresh(); dialog.close(); renderDocuments(document.querySelector('#main'));
    if(!result.confirmed)previewTemplate(result.id);else toast('Template imported.');
  });};
}

// A reconstructed PDF must be compared against its source before it is reused.
function previewTemplate(id) {
  const template=state.data.templates.find(item=>item.id===id);
  const needs=!template.confirmed;
  const body=`<div class="modal-body">${(template.warnings||[]).map(w=>`<div class="notice warning">${h(w)}</div>`).join('')}<div class="${needs?'preview-pair':'template-single'}">${needs?`<div><h3>Original PDF</h3><iframe title="Original template" src="${asset(template.source_path)}"></iframe></div>`:''}<div><h3>${needs?'Reconstructed':'Preview'}</h3><iframe class="editor-preview" title="Editable template preview" src="${asset(template.pdf_path)}"></iframe></div></div></div>`;
  const dialog=modal(template.filename,body,needs?'<button id="confirm-template" class="primary">I checked every page</button>':'','editor-dialog');
  if(needs)dialog.querySelector('#confirm-template').onclick=event=>busy(event.target,async()=>{await api('template/confirm',{id});await refresh();dialog.close();renderDocuments(document.querySelector('#main'));});
}


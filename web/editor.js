import {state,h,asset,api,refresh,task,busy,toast,modal} from './ui.js';
import {requestChat} from './chat.js';

// Edit content inside existing template slots and preview the saved rendered PDF.
export async function openEditor(id,jobId=null) {
  let document;
  try {document=await api('document?id='+encodeURIComponent(id));}catch(error){return toast(error.message,true);}
  if(document.kind==='resume')return previewResume(document);
  const issues=['Unfinished draft. Chat reviews do not approve employer submission.'];
  const body=`<div class="editor-body"><section class="editor-content"><h3>Content</h3>${issues.length?`<div class="notice warning">${issues.map(h).join('<br>')}</div>`:'<div class="notice">Content and formatting checks passed.</div>'}${document.blocks.map(block=>blockField(block)).join('')}</section><iframe class="editor-preview" title="Saved PDF preview" src="${asset(document.pdf_path)}"></iframe></div>`;
  const footer=`<div><span class="muted small">Preview updates after saving.</span><div class="actions"><a href="${asset(document.docx_path)}" download="${h(document.title)}.docx">Download DOCX</a><span class="muted" aria-hidden="true">·</span><a href="${asset(document.pdf_path)}" download="${h(document.title)}.pdf">Download PDF</a></div></div><div class="actions">${document.document_type==='resume'?'':'<button id="agent-toggle">Ask agent</button>'}<button id="save-document" class="primary">Save draft</button></div>`;
  const dialog=modal(document.title,body,footer,'editor-dialog');
  dialog.querySelector('h2').className='editor-title';
  dialog.querySelector('#save-document').onclick=event=>saveDocument(event.target,dialog,document,jobId);
  dialog.querySelector('#agent-toggle')?.addEventListener('click',()=>toggleAgent(dialog,document));
  const close=()=>{if(!dirty(dialog,document)||confirm('Discard unsaved edits?'))dialog.close();};
  dialog.querySelector('.close').onclick=close;
  dialog.oncancel=event=>{event.preventDefault();close();};
}

// Protected Word fields and hyperlinks stay visible but cannot be edited unsafely.
function blockField(block) {
  const label=block.style || (block.part?.includes('header')?'Header':'Paragraph');
  return `<label>${h(label)}${block.editable===false?' · read-only':''}<textarea data-block="${h(block.id)}" ${block.editable===false?'readonly aria-description="Edit this field in the source template."':''} rows="${Math.min(6,Math.max(2,Math.ceil(block.text.length/65)))}">${h(block.text)}</textarea></label>`;
}

// Collect changes only; untouched template XML remains byte-for-byte unchanged.
function edits(dialog,document) {
  const result={};
  for(const field of dialog.querySelectorAll('[data-block]')) {
    const original=document.blocks.find(block=>block.id===field.dataset.block);
    if(field.value!==original.text)result[field.dataset.block]=field.value;
  }
  return result;
}

// Detect local changes before closing a document editor.
function dirty(dialog,document) {return Object.keys(edits(dialog,document)).length>0;}

// Saving a job document invalidates its earlier attachment until preparation runs again.
async function saveDocument(button,dialog,document,jobId) {
  await busy(button,async()=>{
    const result=await task('document/save',{document_id:document.id,job_id:jobId,edits:edits(dialog,document)});
    await refresh();dialog.oncancel=null;dialog.close();await openEditor(result.document_id,jobId);
    toast('Draft saved. Request fresh chat reviews for this version.');
  });
}

// A chat request queues the saved version for the app-controlled draft and review workflow.
function toggleAgent(dialog,document) {
  const body=dialog.querySelector('.editor-body');
  if(body.querySelector('.agent-pane')) {body.querySelector('.agent-pane').remove();body.classList.remove('with-agent');return;}
  body.classList.add('with-agent');
  body.insertAdjacentHTML('beforeend',`<aside class="agent-pane"><h3>Chat edit request</h3><label>Instructions<textarea id="agent-instruction" placeholder="Describe the cover-letter edit"></textarea></label><button id="ask-document-agent" class="primary">Queue request</button><div id="agent-result" aria-live="polite"></div></aside>`);
  body.querySelector('#ask-document-agent').onclick=event=>busy(event.target,async()=>{
    if(dirty(dialog,document))throw new Error('Save your edits before requesting chat changes.');
    const message=body.querySelector('#agent-instruction').value.trim();
    if(!message)throw new Error('Describe the edit you want.');
    await requestChat('document',document.id,message);
    body.querySelector('#agent-result').innerHTML='<p>Queued for the Application Agent.</p><a href="#chat" id="open-draft-chat">Open chat</a>';
    body.querySelector('#open-draft-chat').onclick=()=>dialog.close();
  });
}

// Uploaded resumes are previewed and downloaded exactly as supplied, without an AI editor.
function previewResume(document) {
  const body=`<div class="modal-body template-single"><iframe class="editor-preview" title="Uploaded resume preview" src="${asset(document.pdf_path)}"></iframe></div>`;
  modal(document.filename,body,`<a href="${asset(document.source_path)}" download="${h(document.filename)}">Download original</a>`,'editor-dialog');
}

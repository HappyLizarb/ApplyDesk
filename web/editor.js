import {h,asset,api,toast,modal} from './ui.js';
import {deleteDocument} from './document-deletion.js';

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

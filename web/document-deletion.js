import {state,h,api,refresh,busy,toast,modal} from './ui.js';

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

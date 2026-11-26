// Identify only the observed resume widget; other attachment groups retain normal inspection.
function workdayResumeGroup(element) {
  if(!/^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(location.hostname))return null;
  const group=element.closest('[data-automation-id="attachments-FileUpload"]');
  if(!group||group.querySelectorAll('button[data-automation-id="select-files"][id="resumeAttachments--attachments"]').length!==1
      ||group.querySelectorAll('input[type="file"]').length!==1
      ||!group.querySelector('input[type="file"][data-automation-id="file-upload-input-ref"]'))return null;
  return group;
}

// Keep the container's identity when React replaces or clears its hidden native file input.
function workdayResumeFields(root,identity,clean) {
  return [...root.querySelectorAll('[data-automation-id="attachments-FileUpload"]')]
    .filter(group=>workdayResumeGroup(group)===group&&group.getClientRects().length>0).map(group=>{
      const input=group.querySelector('input[type="file"]'),section=group.closest('[role="group"][aria-labelledby]');
      const headingIds=(section?.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
      const headings=headingIds.map(id=>document.getElementById(id)).filter(Boolean);
      const label=clean(headings.map(heading=>heading.textContent).join(' '));
      const instructionIds=(group.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
      const instructions=instructionIds.map(id=>document.getElementById(id)).filter(Boolean);
      const context=group.closest('form,section,main,[role="form"],[role="dialog"]'),form=input.form;
      return {field_id:identity(group),group_id:identity(group),upload_input_id:identity(input),widget:'workday_resume',
        type:'file',name:'resumeAttachments--attachments',label,description:clean(instructions.map(node=>node.textContent).join(' ')),
        supported:/^resume\s*\/\s*cv$/i.test(label),disabled:input.disabled,readonly:false,multiple:input.multiple,accept:input.accept||'',
        required:input.required||group.getAttribute('aria-required')==='true'
          ||[...headings,...instructions].some(node=>!!node.querySelector('abbr')||/\*\s*$/.test(node.textContent)),
        value:null,files:[...input.files].map(file=>({name:file.name,size:file.size,type:file.type})),
        form_id:form?identity(form):null,context_id:context?identity(context):null};
    });
}
module.exports={workdayResumeGroup,workdayResumeFields};

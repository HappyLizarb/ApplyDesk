import {h,modal} from './ui.js';

const statuses=[['','Choose status'],['us_citizen','US citizen'],['us_national','US national'],
  ['permanent_resident','Permanent resident'],['f1_opt','F-1 OPT'],['f1_stem_opt','F-1 STEM OPT'],
  ['f1_cpt','F-1 CPT'],['other_work_authorized','Other work-authorized status'],['other_unsure','Other / unsure']];

// Hidden fields retain the user's explicit choices in the same unsaved profile draft.
export function authorizationControls(row,index) {
  const active=row.country==='US'&&row.sponsorship_required==='no';
  const status=active?row.immigration_status||'':'',restrictions=active?row.restrictions||'':'';
  const label=statuses.find(([value])=>value===status)?.[1]||'';
  return `<input type="hidden" name="authorizations.${index}.immigration_status" value="${h(status)}"><input type="hidden" name="authorizations.${index}.restrictions" value="${h(restrictions)}"><button type="button" class="quiet authorization-status" data-us-status ${active?'':'hidden'}>${h(status?label:'Add US status')}</button>`;
}

// Country and sponsorship changes clear inactive details before the form reads its draft.
export function bindAuthorization(form) {
  form.addEventListener('change',event=>{
    if(!/^authorizations\.\d+\.(country|sponsorship_required)$/.test(event.target.name||''))return;
    const row=event.target.closest('[data-row="authorizations"]');
    const active=row.querySelector('[name$=".country"]').value==='US'&&row.querySelector('[name$=".sponsorship_required"]').value==='no';
    const button=row.querySelector('[data-us-status]');button.hidden=!active;
    if(active){showStatus(row);return;}
    row.querySelector('[name$=".immigration_status"]').value='';
    row.querySelector('[name$=".restrictions"]').value='';
    button.textContent='Add US status';
  });
  form.addEventListener('click',event=>{
    const button=event.target.closest('[data-us-status]');
    if(button)showStatus(button.closest('[data-row="authorizations"]'));
  });
}

// The popup collects self-reported facts without inferring legal permission or saving the profile.
function showStatus(row) {
  const status=row.querySelector('[name$=".immigration_status"]'),restrictions=row.querySelector('[name$=".restrictions"]');
  const body=`<div class="modal-body authorization-details"><label for="us-immigration-status">US immigration status<select id="us-immigration-status">${statuses.map(([value,label])=>`<option value="${value}" ${value===status.value?'selected':''}>${label}</option>`).join('')}</select></label><label for="us-work-restrictions">Status details and work restrictions (optional)<textarea id="us-work-restrictions" rows="3" maxlength="10000" placeholder="For example: field of study, employer, authorized dates or hours">${h(restrictions.value)}</textarea></label></div>`;
  const dialog=modal('US work authorization',body,'<button type="button" data-cancel-status>Cancel</button><button type="button" class="primary" data-use-status>Use status</button>','authorization-dialog');
  const choice=dialog.querySelector('#us-immigration-status'),apply=dialog.querySelector('[data-use-status]');
  apply.disabled=!choice.value;
  choice.addEventListener('change',()=>{apply.disabled=!choice.value;});
  dialog.querySelector('[data-cancel-status]').addEventListener('click',()=>dialog.close());
  apply.addEventListener('click',()=>{
    status.value=choice.value;restrictions.value=dialog.querySelector('#us-work-restrictions').value.trim();
    row.querySelector('[data-us-status]').textContent=statuses.find(([value])=>value===status.value)[1];
    dialog.close();status.dispatchEvent(new Event('change',{bubbles:true}));
  });
}

import {h} from './ui.js';

const months=Array.from({length:12},(_,index)=>[String(index+1).padStart(2,'0'),new Intl.DateTimeFormat('en',{month:'long',timeZone:'UTC'}).format(new Date(Date.UTC(2000,index,1)))]);

// Keep year-only and legacy date text intact until the user explicitly supplies a month.
export function educationDate(name,label,value) {
  const match=/^(\d{4})(?:-(0[1-9]|1[0-2]))?$/.exec(value);
  const year=match?match[1]:value,month=match?.[2]||'';
  return `<fieldset class="education-date"><legend>${h(label)}</legend><div class="date-fields">
    <select name="${name}.month" aria-label="${h(label)} month"><option value="">Month</option>${months.map(([v,t])=>`<option value="${v}" ${v===month?'selected':''}>${t}</option>`).join('')}</select>
    <input name="${name}.year" aria-label="${h(label)} year" type="text" value="${h(year)}" placeholder="${name.endsWith('.end')?'Year or Present':'Year'}" maxlength="500">
    </div></fieldset>`;
}

// Serialize the visible controls into the existing date field without guessing missing precision.
export function readEducationDate(form,name) {
  const year=form.elements.namedItem(name+'.year'),month=form.elements.namedItem(name+'.month').value;
  const value=year.value.trim(),valid=!month||/^\d{4}$/.test(value);
  year.setCustomValidity(valid?'':'Enter a four-digit year, or clear the month to use Present.');
  return month&&valid?`${value}-${month}`:value;
}

// Native disclosure and checkboxes provide a keyboard-accessible multiple-selection dropdown.
export function levelPicker(levels,selected) {
  return `<details class="level-picker"><summary aria-label="Choose job levels"><span class="level-tags"></span><span aria-hidden="true">⌄</span></summary>
    <div class="level-menu">${levels.map(level=>`<label class="check-label"><input type="checkbox" name="levels" value="${h(level)}" ${selected.some(item=>item.toLowerCase()===level.toLowerCase())?'checked':''}>${h(level)}</label>`).join('')}</div></details>`;
}

// Change only the selected-tag display so focus stays on the checkbox being edited.
export function updateLevelTags(form) {
  const tags=form.querySelector('.level-tags');
  const selected=[...form.querySelectorAll('[name=levels]:checked')].map(input=>input.value);
  tags.innerHTML=selected.length?selected.map(level=>`<span class="level-tag">${h(level)}</span>`).join(''):'Select job levels';
}

// Escape closes the menu and restores focus; clicks elsewhere within the form also dismiss it.
export function bindLevelPicker(form) {
  const picker=form.querySelector('.level-picker');
  updateLevelTags(form);
  form.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&picker.open){picker.open=false;picker.querySelector('summary').focus();event.preventDefault();}
  });
  form.addEventListener('click',event=>{if(!picker.contains(event.target))picker.open=false;});
}

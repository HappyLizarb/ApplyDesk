import {h} from './ui.js';

const months=Array.from({length:12},(_,index)=>[String(index+1).padStart(2,'0'),new Intl.DateTimeFormat('en',{month:'short',timeZone:'UTC'}).format(new Date(Date.UTC(2000,index,1)))]);

// Retain the precision the applicant supplied, including partial and legacy dates.
export function educationDate(name,label,value) {
  const match=/^(\d{4})(?:-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?)?$/.exec(value);
  const year=match?match[1]:value,month=match?.[2]||'',day=match?.[3]||'';
  return `<fieldset class="education-date"><legend class="sr-only">${h(label)}</legend><div class="date-fields">
    <select name="${name}.month" aria-label="${h(label)} month"><option value="">Month</option>${months.map(([v,t])=>`<option value="${v}" ${v===month?'selected':''}>${t}</option>`).join('')}</select>
    <input name="${name}.day" aria-label="${h(label)} day (optional)" type="text" inputmode="numeric" value="${h(day)}" placeholder="Day" size="3" maxlength="2">
    <input name="${name}.year" aria-label="${h(label)} year" type="text" value="${h(year)}" placeholder="${name.endsWith('.end')?'Year or Present':'Year'}" size="${Math.max(4,year.length)}" maxlength="500">
    </div></fieldset>`;
}

// Serialize the visible controls into the existing date field without guessing missing precision.
export function readEducationDate(form,name) {
  const year=form.elements.namedItem(name+'.year'),month=form.elements.namedItem(name+'.month').value;
  const day=form.elements.namedItem(name+'.day'),digits=day.value.trim();
  const value=year.value.trim(),valid=!month||/^\d{4}$/.test(value);
  year.setCustomValidity(valid?'':'Enter a four-digit year, or clear the month to use Present.');
  const maxDay=valid&&month?new Date(Date.UTC(Number(value),Number(month),0)).getUTCDate():0;
  const validDay=!digits||(/^\d{1,2}$/.test(digits)&&Number(digits)>=1&&Number(digits)<=maxDay);
  day.setCustomValidity(validDay?'':'Choose a month and year and enter a valid day for that month.');
  if(digits&&validDay)return `${value}-${month}-${digits.padStart(2,'0')}`;
  return month&&valid?`${value}-${month}`:value;
}

// Native disclosure and checkboxes provide a keyboard-accessible multiple-selection dropdown.
export function levelPicker(levels,selected,name='levels') {
  return `<details class="level-picker"><summary aria-label="Choose job levels sought"><span class="level-tags"></span><span aria-hidden="true">⌄</span></summary>
    <div class="level-menu">${levels.map(level=>`<label class="check-label"><input type="checkbox" name="${h(name)}" value="${h(level)}" ${selected.some(item=>item.toLowerCase()===level.toLowerCase())?'checked':''}>${h(level)}</label>`).join('')}</div></details>`;
}

// Change only the selected-tag display so focus stays on the checkbox being edited.
export function updateLevelTags(form) {
  for(const picker of form.querySelectorAll('.level-picker')){
    const selected=[...picker.querySelectorAll('input:checked')].map(input=>input.value);
    picker.querySelector('.level-tags').innerHTML=selected.length?selected.map(level=>`<span class="level-tag">${h(level)}</span>`).join(''):'Job levels sought';
  }
}

// Escape restores focus to the open picker; clicks elsewhere dismiss only unrelated menus.
export function bindLevelPicker(form) {
  updateLevelTags(form);
  form.addEventListener('keydown',event=>{
    const picker=event.target.closest?.('.level-picker');
    if(event.key==='Escape'&&picker?.open){picker.open=false;picker.querySelector('summary').focus();event.preventDefault();}
  });
  form.addEventListener('click',event=>{
    form.querySelectorAll('.level-picker').forEach(picker=>{if(!picker.contains(event.target))picker.open=false;});
  });
}

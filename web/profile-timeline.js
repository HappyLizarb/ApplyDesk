import {h} from './ui.js';

export const entryFields={education:['institution','degree','field','gpa','gpa_scale','start','end','coursework'],experiences:['title','company','job_type','start','end','location','details']};
const longMonths=['January','February','March','April','May','June','July','August','September','October','November','December'];
const monthOptions=longMonths.map((month,index)=>[String(index+1).padStart(2,'0'),month.slice(0,3)]);
const fieldLabels={institution:'School name',degree:'Degree',field:'Major / field of study',gpa:'GPA (optional)',gpa_scale:'GPA scale (optional)',coursework:'Relevant coursework',title:'Job title',company:'Employer',job_type:'Job type',start:'Start date',end:'End date',location:'Location',details:'Description'};

// Month-specific ISO values get a readable label; partial or unfamiliar dates stay literal.
export function displayDate(value='') {
  const match=/^(\d{4})-(0[1-9]|1[0-2])$/.exec(value);
  return match?`${monthOptions[Number(match[2])-1][1]} ${match[1]}`:value;
}

// Normalize only explicit month names, preserving year-only and legacy date descriptions.
export function storedDate(value='') {
  const text=value.trim(),match=/^([A-Za-z]+)\s+(\d{4})$/.exec(text);
  if(!match)return text;
  const month=longMonths.findIndex(month=>[month,month.slice(0,3)].some(name=>name.toLowerCase()===match[1].toLowerCase()));
  return month<0?text:`${match[2]}-${String(month+1).padStart(2,'0')}`;
}

// Count inclusive calendar months only when both endpoints have explicit month precision.
export function experienceDuration(start,end,today=new Date()) {
  const normalizedEnd=/^present$/i.test(end.trim())?`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}`:storedDate(end);
  const pattern=/^(\d{4})-(0[1-9]|1[0-2])$/;
  const first=pattern.exec(storedDate(start)),last=pattern.exec(normalizedEnd);
  if(!first||!last)return '';
  const count=(Number(last[1])-Number(first[1]))*12+Number(last[2])-Number(first[2])+1;
  if(count<=0)return '';
  const years=Math.floor(count/12),remaining=count%12;
  return [years?`${years} ${years===1?'yr':'yrs'}`:'',remaining?`${remaining} ${remaining===1?'mo':'mos'}`:''].filter(Boolean).join(' ');
}

// Retain the precision the applicant supplied, including partial and legacy dates.
function educationDate(name,label,value) {
  const match=/^(\d{4})(?:-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?)?$/.exec(value);
  const year=match?match[1]:value,month=match?.[2]||'',day=match?.[3]||'';
  return `<fieldset class="education-date"><legend class="sr-only">${h(label)}</legend><div class="date-fields">
    <select name="${name}.month" aria-label="${h(label)} month"><option value="">Month</option>${monthOptions.map(([code,label])=>`<option value="${code}" ${code===month?'selected':''}>${label}</option>`).join('')}</select>
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

// Native inputs remain directly editable while visible content follows the profile hierarchy.
function entryInput(section,index,key,value='') {
  const id=`profile-${section}-${index}-${key}`,label=fieldLabels[key];
  const title=['title','institution'].includes(key),date=['start','end'].includes(key);
  if(section==='education'&&date)return educationDate(`${section}.${index}.${key}`,label,value);
  const placeholder=key==='end'?'End date or Present':key==='gpa'?'GPA':key==='gpa_scale'?'Scale':label;
  const content=date?displayDate(value):value;
  const control=['details','coursework'].includes(key)?`<textarea id="${id}" name="${section}.${index}.${key}" rows="${key==='coursework'?2:3}" maxlength="10000" placeholder="${label}">${h(content)}</textarea>`:
    `<input id="${id}" name="${section}.${index}.${key}" type="text" value="${h(content)}" placeholder="${h(placeholder)}" ${date?`size="${Math.max(2,String(content||placeholder).length)}"`:""} maxlength="10000">`;
  const kind=title?'title':date?'date':key==='details'?'description':key.replace('_','-');
  return `<div class="entry-${kind}"><label class="sr-only" for="${id}">${label}</label>${control}</div>`;
}

// Education and employment share readable title, metadata, date and description rows.
export function timelineRow(section,row,index) {
  const input=key=>entryInput(section,index,key,row[key]||'');
  const duration=section==='experiences'?experienceDuration(row.start||'',row.end||''):'';
  const dates=`<div class="entry-line entry-dates">${input('start')}<span aria-hidden="true">–</span>${input('end')}${section==='experiences'?`<output class="entry-duration" aria-label="Duration">${duration?'· '+h(duration):''}</output>`:''}</div>`;
  const metadata=section==='education'?['degree','field','gpa']:['company','job_type'];
  const metadataInput=key=>key==='gpa'?`<div class="entry-gpa-pair">${input('gpa')}<span aria-hidden="true">/</span>${input('gpa_scale')}</div>`:input(key);
  const subtitle=`<div class="entry-line ${section==='education'?'entry-education-meta':'entry-employer'}">${metadata.map(metadataInput).join('<span aria-hidden="true">·</span>')}</div>`;
  const location=section==='experiences'?input('location'):'';
  const title=section==='education'?'institution':'title';
  return `<div class="profile-entry" data-row="${section}"><div class="entry-body">${input(title)}${subtitle}${dates}${location}${input(section==='education'?'coursework':'details')}</div><button type="button" class="entry-remove quiet danger" data-remove="${section}" data-index="${index}" aria-label="Remove ${section==='experiences'?'experience':'education'} entry ${index+1}">Remove</button></div>`;
}

// Recalculate visible durations without replacing the field or moving its caret.
export function updateEntryDurations(form) {
  form.querySelectorAll('.entry-date input').forEach(input=>{
    input.size=Math.max(2,(input.value||input.placeholder).length);
  });
  form.querySelectorAll('[data-row="experiences"]').forEach(row=>{
    const start=row.querySelector('[name$=".start"]').value,end=row.querySelector('[name$=".end"]').value;
    const duration=experienceDuration(start,end);
    row.querySelector('.entry-duration').textContent=duration?'· '+duration:'';
  });
}

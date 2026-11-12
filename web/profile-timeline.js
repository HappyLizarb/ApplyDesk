import {h} from './ui.js';

export const entryFields={experiences:['title','company','job_type','start','end','location','details'],projects:['name','start','end','details','url']};
const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const longMonths=['January','February','March','April','May','June','July','August','September','October','November','December'];
const fieldLabels={title:'Job title',company:'Employer',job_type:'Job type',start:'Start date',end:'End date',location:'Location',details:'Description',name:'Project title',url:'Project URL'};

// Month-specific ISO values get a readable label; partial or unfamiliar dates stay literal.
export function displayDate(value='') {
  const match=/^(\d{4})-(0[1-9]|1[0-2])$/.exec(value);
  return match?`${months[Number(match[2])-1]} ${match[1]}`:value;
}

// Normalize only explicit month names, preserving year-only and legacy date descriptions.
export function storedDate(value='') {
  const text=value.trim(),match=/^([A-Za-z]+)\s+(\d{4})$/.exec(text);
  if(!match)return text;
  const month=months.findIndex((name,index)=>[name.toLowerCase(),longMonths[index].toLowerCase()].includes(match[1].toLowerCase()));
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

// Native inputs remain directly editable while visible content follows the profile hierarchy.
function entryInput(section,index,key,value='') {
  const id=`profile-${section}-${index}-${key}`,label=fieldLabels[key];
  const title=['title','name'].includes(key),date=['start','end'].includes(key);
  const placeholder=key==='end'?'End date or Present':key==='url'?'https://':label;
  const content=date?displayDate(value):value;
  const control=key==='details'?`<textarea id="${id}" name="${section}.${index}.${key}" rows="3" maxlength="10000" placeholder="${label}">${h(content)}</textarea>`:
    `<input id="${id}" name="${section}.${index}.${key}" type="${key==='url'?'url':'text'}" value="${h(content)}" placeholder="${h(placeholder)}" ${date?`size="${Math.max(2,String(content||placeholder).length)}"`:""} maxlength="10000">`;
  const kind=title?'title':date?'date':key==='details'?'description':key.replace('_','-');
  return `<div class="entry-${kind}"><label class="sr-only" for="${id}">${label}</label>${control}</div>`;
}

// Experience and project rows share controls but retain their distinct requested line order.
export function timelineRow(section,row,index) {
  const input=key=>entryInput(section,index,key,row[key]||'');
  const duration=section==='experiences'?experienceDuration(row.start||'',row.end||''):'';
  const dates=`<div class="entry-line entry-dates">${input('start')}<span aria-hidden="true">–</span>${input('end')}${section==='experiences'?`<output class="entry-duration" aria-label="Duration">${duration?'· '+h(duration):''}</output>`:''}</div>`;
  const employer=section==='experiences'?`<div class="entry-line entry-employer">${input('company')}<span aria-hidden="true">·</span>${input('job_type')}</div>`:'';
  const location=section==='experiences'?input('location'):'';
  const url=section==='projects'?input('url'):'';
  return `<div class="profile-entry" data-row="${section}"><div class="entry-body">${input(section==='experiences'?'title':'name')}${employer}${dates}${location}${input('details')}${url}</div><button type="button" class="entry-remove quiet danger" data-remove="${section}" data-index="${index}" aria-label="Remove ${section==='experiences'?'experience':'project'} entry ${index+1}">Remove</button></div>`;
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

import assert from 'node:assert/strict';
import {test} from 'node:test';
import {displayDate,storedDate,experienceDuration,timelineRow,updateEntryDurations}
  from '../web/profile-timeline.js';

// The visible month labels must save back to the same explicit calendar month.
test('all canonical months round trip through their display labels',()=>{
  const labels=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  labels.forEach((label,index)=>{
    const date=`2025-${String(index+1).padStart(2,'0')}`;
    assert.equal(displayDate(date),`${label} 2025`);
    assert.equal(storedDate(displayDate(date)),date);
  });
  assert.equal(storedDate('  September 2024  '),'2024-09');
  assert.equal(storedDate('jANuArY 2023'),'2023-01');
});

// Partial and legacy dates retain the precision the applicant originally supplied.
test('year-only, unknown, and invalid dates remain literal',()=>{
  for(const value of ['2024','Present','Spring 2024','2024-00','2024-13','2024-06-04','Unknown','']){
    assert.equal(displayDate(value),value);
    assert.equal(storedDate(value),value);
  }
  assert.equal(storedDate('  During college  '),'During college');
  assert.equal(storedDate('June 24'),'June 24');
});

// LinkedIn-style durations count inclusive calendar months rather than fabricated days.
test('duration handles one month, whole years, and mixed year/month ranges',()=>{
  assert.equal(experienceDuration('2025-01','2025-01'),'1 mo');
  assert.equal(experienceDuration('2025-01','2025-03'),'3 mos');
  assert.equal(experienceDuration('2024-01','2024-12'),'1 yr');
  assert.equal(experienceDuration('2023-12','2025-01'),'1 yr 2 mos');
  assert.equal(experienceDuration('Jan 2023','December 2024'),'2 yrs');
});

// Present uses the caller's local calendar month; unknown or reversed ranges show no claim.
test('duration supports Present without inferring missing months',()=>{
  const today=new Date(2026,10,1,0,1);
  assert.equal(experienceDuration('2026-10',' pReSeNt ',today),'2 mos');
  for(const [start,end] of [['2024','2025-01'],['2024-01','2025'],['Spring 2024','Present'],
    ['','Present'],['2025-02','2025-01'],['2024-13','2025-01'],['2026-12','Present']]){
    assert.equal(experienceDuration(start,end,today),'');
  }
});

// Every visible value remains an escaped native control using the existing save field names.
test('experience row exposes all editable fields in the requested order',()=>{
  const row={title:'<Design> "intern"',company:'A & B',job_type:'Internship',start:'2025-06',
    end:'2025-08',location:'Remote',details:'Built <forms>\nReviewed accessibility.'};
  const html=timelineRow('experiences',row,2);
  const fields=['title','company','job_type','start','end','location','details'];
  const positions=fields.map(key=>html.indexOf(`name="experiences.2.${key}"`));
  assert.ok(positions.every((position,index)=>position>=0&&(!index||position>positions[index-1])));
  assert.match(html,/data-row="experiences"/);
  assert.match(html,/value="&lt;Design&gt; &quot;intern&quot;"/);
  assert.match(html,/value="A &amp; B"/);
  assert.match(html,/value="Jun 2025"/);
  assert.match(html,/Built &lt;forms&gt;\nReviewed accessibility\./);
  assert.match(html,/aria-label="Duration">· 3 mos<\/output>/);
});

// Project links are editable text values, so hostile imported URLs cannot create active links.
test('project row keeps date precision and safely renders description and URL',()=>{
  const html=timelineRow('projects',{name:'Thesis',start:'2024',end:'Present',
    details:'</textarea><script>alert(1)</script>',url:'" onfocus="alert(1)'},0);
  for(const key of ['name','start','end','details','url'])assert.ok(html.includes(`name="projects.0.${key}"`));
  assert.match(html,/value="2024"/);
  assert.match(html,/value="Present"/);
  assert.match(html,/&lt;\/textarea&gt;&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html,/value="&quot; onfocus=&quot;alert\(1\)"/);
  assert.doesNotMatch(html,/<a\b|<script>|entry-duration|\.company"/);
});

// Duration refresh changes only the output, leaving typed values and caret-bearing inputs alone.
test('editing dates refreshes and clears the duration without replacing inputs',()=>{
  const start={value:'Jan 2025'},end={value:'Mar 2025'},output={textContent:''};
  const row={querySelector:selector=>selector.includes('.start')?start:selector.includes('.end')?end:output};
  const form={querySelectorAll:selector=>selector==='.entry-date input'?[start,end]:[row]};
  updateEntryDurations(form);
  assert.equal(output.textContent,'· 3 mos');
  assert.equal(start.value,'Jan 2025');
  end.value='2025';
  updateEntryDurations(form);
  assert.equal(output.textContent,'');
  assert.equal(end.value,'2025');
});

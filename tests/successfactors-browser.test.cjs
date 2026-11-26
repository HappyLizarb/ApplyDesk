const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {start,dispatch}=require('../browser/worker.cjs');
// The hidden entry permit must not unlock any other native form.
(async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-handoff-'));
 const session=await start({dataRoot:root,headless:true,allowLocal:true});let posts=0;
 try{
  await session.context.route('**/*',route=>{
   if(route.request().method()==='POST')posts++;
   return route.fulfill({contentType:'text/html',body:'<form id="entry" method="post" action="/entry"><input name="token" value="fixture" type="hidden"></form><form id="other" method="post" action="/other"></form>'});
  });
  await dispatch(session,'open',{url:'https://jobs.supermicro.com/job/role/1000000001/'});
  await session.page.evaluate(()=>{document.querySelector('#entry').requestSubmit()});
  await session.page.waitForTimeout(100);assert.equal(posts,0);
  await session.page.evaluate(()=>{const s=globalThis.__applyDesk;s.entryForm=document.querySelector('#entry');s.entryUntil=Date.now()+10000;document.querySelector('#other').requestSubmit()});
  await session.page.waitForTimeout(100);assert.equal(posts,0);
  await session.page.evaluate(()=>{globalThis.__applyDesk.entryUntil=0;document.querySelector('#entry').requestSubmit()});
  await session.page.waitForTimeout(100);assert.equal(posts,0);
  await session.page.evaluate(()=>{globalThis.__applyDesk.entryUntil=Date.now()+10000;document.querySelector('#entry').requestSubmit()});
  await session.page.waitForURL('**/entry');assert.equal(posts,1);
  console.log('Hidden handoff permission is limited to the exact native form and expires.');
 }finally{await dispatch(session,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

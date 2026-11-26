const assert=require('node:assert/strict');
const http=require('node:http');
const {chromium}=require('playwright');
const {installOwnedPageGuards,disposeOwnedPageGuards}=require('../browser/guard.cjs');
const {inspect}=require('../browser/inspect.cjs');
const {advance}=require('../browser/actions.cjs');

// Real native form posts prove that a nonfinal step can save without a site-specific network permit.
async function fixture() {
  const posts=[];
  const server=http.createServer((request,response)=>{
    if(request.method==='POST')posts.push(request.url);
    response.setHeader('Content-Type','text/html');
    if(request.url==='/entry')return response.end('<main><button type="button" onclick="location.href=\'/\'">Continue</button></main>');
    if(request.url==='/review')return response.end('<main><h1>Application review</h1><button>Submit application</button></main>');
    response.end('<main><form method="POST" action="/review"><label>First name<input name="first" value="Example"></label><button>Save and Continue</button></form></main>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  return {server,posts,url:`http://127.0.0.1:${server.address().port}`};
}

// A missing audit cannot advance; a fresh audit can, and the final control remains a separate action.
async function run() {
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const test=await fixture();
  const page=await browser.newPage();
  const state={normalNetworking:true,allowLocal:true,paused:false,generation:1,blocked:[],manual:false};
  try {
    await installOwnedPageGuards(page,state);await page.goto(test.url+'/entry');
    const entry=await inspect(page,state);
    await advance(page,state,{snapshot_id:entry.snapshot_id,field_id:entry.buttons[0].field_id});
    let snapshot=await inspect(page,state),button=snapshot.buttons.find(item=>item.action==='advance');
    const params={snapshot_id:snapshot.snapshot_id,field_id:button.field_id};
    await assert.rejects(advance(page,state,params),/Review the current fields/);
    assert.deepEqual(test.posts,[]);
    snapshot=await advance(page,state,{...params,audit:{passed:true,snapshot_id:snapshot.snapshot_id,field_ids:snapshot.fields.map(field=>field.field_id)}});
    assert.deepEqual(test.posts,['/review']);assert.match(snapshot.text,/Application review/);
    const final=snapshot.buttons.find(item=>item.action==='final_or_ambiguous');
    await assert.rejects(advance(page,state,{snapshot_id:snapshot.snapshot_id,field_id:final.field_id,audit:{passed:true,snapshot_id:snapshot.snapshot_id}}),/final submission boundary/);
    assert.deepEqual(test.posts,['/review']);
  } finally {await disposeOwnedPageGuards(page,state);await browser.close();await new Promise(resolve=>test.server.close(resolve));}
  console.log('Normal Chrome navigation requires a current audit, saves native nonfinal steps and cannot advance through final submission.');
}
run().catch(error=>{console.error(error);process.exitCode=1});

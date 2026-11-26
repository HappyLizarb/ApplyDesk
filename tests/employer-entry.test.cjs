const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start,dispatch} = require('../browser/worker.cjs');

// The application opens through the employer's actual click handler, not its fallback link.
(async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-employer-entry-'));
  const session=await start({dataRoot:root,headless:true,allowLocal:true});
  try {
    await session.context.route('**/*',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:`
      <h1>Fixture Systems Engineer</h1><p>Supermicro</p>
      <a class="dialogApplyBtn" href="/talentcommunity/apply/1000000001/?locale=en_US">Apply now »</a>
      <script>document.querySelector('a').onclick=e=>{e.preventDefault();document.body.insertAdjacentHTML('beforeend','<label>Email<input type="email"></label>')}</script>`}));
    const page=await dispatch(session,'open',{url:'https://jobs.supermicro.com/job/Example/1000000001/'});
    const params={snapshot_id:page.snapshot_id,link_id:page.links[0].id,company:'Supermicro',title:'Fixture Systems Engineer'};
    await assert.rejects(dispatch(session,'application_entry',{...params,title:'Different job'}),/entry changed/);
    const after=await dispatch(session,'application_entry',params);
    assert.equal(after.url,page.url);
    assert.equal(after.fields.find(field=>field.type==='email').label,'Email');
    console.log('Employer Apply invokes its observed handler and preserves role identity checks.');
  }finally{await dispatch(session,'close',{});await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});

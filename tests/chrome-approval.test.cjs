const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const {connectApprovedChrome}=require('../browser/chrome-approval.cjs');
const options={applicationWorkspace:true,existingChrome:true,autoApproveChrome:true};

// This executable only records its lease; it has no native UI or browser capabilities.
const HELPER=`#!${process.execPath}
const fs=require('node:fs');const append=text=>fs.appendFileSync(process.env.APPROVAL_TEST_LOG,text+'\\n');
append('helper:'+process.pid);
const mode=process.env.APPROVAL_TEST_MODE;
if(mode==='preexisting'||mode==='permission'){console.log(mode==='permission'?'accessibility_required':'manual');process.exit(0)}
console.log('ready');
process.stdin.on('data',data=>{if(String(data)==='arm\\n'){append('armed');setTimeout(()=>append('clicked'),200)}});
process.stdin.on('end',()=>process.exit(0));`;

// Each check gets a separate temporary log and bounded polling of its fictional helper.
async function fixture(directory,name,mode='ready') {
  const log=path.join(directory,name+'.log');
  process.env.APPROVAL_TEST_LOG=log;process.env.APPROVAL_TEST_MODE=mode;
  const read=async()=>(await fs.readFile(log,'utf8').catch(()=>''));
  const wait=async text=>{
    for(let index=0;index<150;index++) {
      if((await read()).includes(text))return;
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    throw new Error('Approval fixture did not reach '+text);
  };
  return {log,read,wait};
}

// No helper starts without opt-in; pre-existing dialogs and missing Accessibility remain manual.
async function manualChecks(directory) {
  for(const mode of ['disabled','preexisting','permission']) {
    const test=await fixture(directory,mode,mode),browser={};
    const result=await connectApprovedChrome({connectOverCDP:async()=>browser},
      {...options,autoApproveChrome:mode!=='disabled'},'ws://127.0.0.1:9222/devtools/browser/fixture');
    assert.equal(result,browser);assert(!(await test.read()).includes('armed'));
    if(mode==='disabled')assert.equal(await test.read(),'');
  }
  const test=await fixture(directory,'permission-error','permission');
  await assert.rejects(connectApprovedChrome({connectOverCDP:async()=>{throw new Error('fixture');}},options,
    'ws://127.0.0.1:9222/devtools/browser/fixture'),/System Settings > Privacy & Security > Accessibility/);
  assert(!(await test.read()).includes('armed'));
}

// A settled connection ends the approval lease before a future lookalike prompt could be pressed.
async function settledChecks(directory) {
  const test=await fixture(directory,'settled'),browser={};let connectStarted=false;
  const connection=connectApprovedChrome({connectOverCDP:async()=>{
    connectStarted=true;await fs.appendFile(test.log,'connecting\n');await test.wait('armed');return browser;
  }},options,'ws://127.0.0.1:9222/devtools/browser/fixture');
  assert.equal(await connection,browser);assert(connectStarted);
  await new Promise(resolve=>setTimeout(resolve,250));
  const log=await test.read();assert(log.indexOf('connecting')<log.indexOf('armed'));assert(!log.includes('clicked'));
}

// Stop kills the waiting helper and closes a late CDP result instead of adopting that connection.
async function cancellationChecks(directory) {
  const test=await fixture(directory,'canceled'),controller=new AbortController();let resolveConnection,closed=0;
  const connecting=new Promise(resolve=>{resolveConnection=resolve;});
  const result=connectApprovedChrome({connectOverCDP:()=>connecting},options,
    'ws://127.0.0.1:9222/devtools/browser/fixture',controller.signal);
  const rejected=assert.rejects(result,/Application paused/);
  await test.wait('armed');controller.abort(new Error('Application paused'));await rejected;
  resolveConnection({close:async()=>{closed++;}});
  await new Promise(resolve=>setTimeout(resolve,250));
  assert.equal(closed,1);assert(!(await test.read()).includes('clicked'));
}

// A real JSONL worker uses a permanently pending fake CDP handshake to exercise startup shutdown.
const RUNNER=`const {runProtocol}=require('./browser/protocol.cjs');
const {connectApprovedChrome}=require('./browser/chrome-approval.cjs');
runProtocol({applicationWorkspace:true,existingChrome:true,autoApproveChrome:true},
  (options,startup)=>connectApprovedChrome({connectOverCDP:()=>new Promise(()=>{})},options,
    'ws://127.0.0.1:9222/devtools/browser/fixture',startup.signal),async()=>{},error=>error.message)
  .catch(error=>{console.error(error);process.exitCode=1});`;

// Stop, closed input and SIGTERM all revoke approval before the worker exits promptly.
async function protocolChecks(directory,mode) {
  const test=await fixture(directory,'protocol-'+mode),child=spawn(process.execPath,['-e',RUNNER],
    {cwd:path.join(__dirname,'..'),env:process.env,stdio:['pipe','pipe','pipe']});
  let output='',errors='';child.stdout.on('data',chunk=>{output+=chunk;});child.stderr.on('data',chunk=>{errors+=chunk;});
  const exited=new Promise(resolve=>child.once('exit',code=>resolve(code)));
  const timeout=setTimeout(()=>child.kill('SIGKILL'),5000);
  try {
    child.stdin.write('{"id":1,"method":"open","params":{}}\n');
    await test.wait('armed');
    if(mode==='stop')child.stdin.write('{"method":"interrupt"}\n');
    if(mode==='eof')child.stdin.end();
    if(mode==='signal')child.kill('SIGTERM');
    assert.equal(await exited,0);assert.equal(errors,'');
    if(mode!=='signal')assert.match(output,/Application paused/);
    await new Promise(resolve=>setTimeout(resolve,250));assert(!(await test.read()).includes('clicked'));
  } finally {clearTimeout(timeout);if(child.exitCode===null)child.kill('SIGKILL');await exited;}
}

async function main() {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-chrome-approval-'));
  const original=process.env.APPLYDESK_LAUNCHER;
  try {
    const launcher=path.join(directory,'fictional-launcher');await fs.writeFile(launcher,HELPER,{mode:0o700});
    process.env.APPLYDESK_LAUNCHER=launcher;
    await manualChecks(directory);await settledChecks(directory);await cancellationChecks(directory);
    for(const mode of ['stop','eof','signal'])await protocolChecks(directory,mode);
    console.log('Chrome approval lifecycle passed: opt-in, manual fallback, pending-only lease, Stop/EOF/SIGTERM and late-CDP cleanup.');
  } finally {
    if(original===undefined)delete process.env.APPLYDESK_LAUNCHER;else process.env.APPLYDESK_LAUNCHER=original;
    delete process.env.APPROVAL_TEST_LOG;delete process.env.APPROVAL_TEST_MODE;
    await fs.rm(directory,{recursive:true,force:true});
  }
}

main().catch(error=>{console.error(error);process.exitCode=1;});

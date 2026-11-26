const {spawn}=require('node:child_process');
const readline=require('node:readline');
const path=require('node:path');

// The native watcher is opt-in and is unavailable outside the signed-in macOS application flow.
async function prepareApproval(options,endpoint,signal) {
  const packaged=__dirname.endsWith('/Contents/Resources/Program/browser')?path.resolve(__dirname,'../../../MacOS/ApplyDesk'):null;
  const launcher=process.env.APPLYDESK_LAUNCHER||packaged;
  if(options.autoApproveChrome!==true||!options.applicationWorkspace||options.existingChrome!==true||options.headless||
    process.platform!=='darwin'||!launcher||options.allowLocal)return {status:'manual'};
  signal?.throwIfAborted();
  const child=spawn(launcher,['--approve-chrome-debugging',endpoint],{stdio:['pipe','pipe','ignore']});
  const lines=readline.createInterface({input:child.stdout});
  const watcher={status:'manual',child};
  watcher.stop=()=>{
    signal?.removeEventListener('abort',watcher.stop);lines.close();
    child.stdin.end();if(child.exitCode===null)child.kill('SIGTERM');
  };
  watcher.arm=()=>{
    if(watcher.status==='ready'&&!signal?.aborted&&child.exitCode===null)child.stdin.write('arm\n');
  };
  child.stdin.on('error',()=>{});
  signal?.addEventListener('abort',watcher.stop,{once:true});
  watcher.status=await new Promise(resolve=>{
    const timer=setTimeout(()=>{watcher.stop();resolve('manual');},3000);
    const finish=status=>{clearTimeout(timer);resolve(status);};
    lines.once('line',line=>finish(['ready','accessibility_required'].includes(line)?line:'manual'));
    child.once('error',()=>finish('manual'));child.once('exit',()=>finish('manual'));
  });
  if(watcher.status!=='ready')watcher.stop();
  return watcher;
}

// Canceling startup revokes the native watcher and rejects promptly without taking browser ownership.
async function connectApprovedChrome(chromium,options,endpoint,signal) {
  const watcher=await prepareApproval(options,endpoint,signal);
  let onAbort;
  try {
    signal?.throwIfAborted();
    const connecting=chromium.connectOverCDP(endpoint,{noDefaults:true,isLocal:true,timeout:180000});
    const canceled=new Promise((resolve,reject)=>{
      onAbort=()=>reject(signal.reason);signal?.addEventListener('abort',onAbort,{once:true});
    });
    connecting.then(browser=>{if(signal?.aborted)browser.close().catch(()=>{});}).catch(()=>{});
    watcher.arm?.();
    return await Promise.race([connecting,canceled]);
  } catch(error) {
    if(signal?.aborted)throw signal.reason;
    const permission=watcher.status==='accessibility_required'
      ? ' For automatic approval, enable ApplyDesk in System Settings > Privacy & Security > Accessibility.' : '';
    throw new Error('Allow ApplyDesk to connect in Chrome. Check chrome://inspect/#remote-debugging and reconnect.'+permission);
  } finally {
    signal?.removeEventListener('abort',onAbort);watcher.stop?.();
  }
}

module.exports={connectApprovedChrome,prepareApproval};

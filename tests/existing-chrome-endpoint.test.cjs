const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {chromeEndpoint}=require('../browser/existing-chrome.cjs');
(async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'applydesk-chrome-endpoint-')),file=path.join(directory,'DevToolsActivePort');
  try {
    await assert.rejects(chromeEndpoint(file),/enable remote debugging/);
    for(const value of ['0\n/devtools/browser/abc','65536\n/devtools/browser/abc','9222\n/devtools/page/abc',
      '9222\n/devtools/browser/abc?token=secret','9222\n/devtools/browser/abc\nother','-1\n/devtools/browser/abc']) {
      await fs.writeFile(file,value);await assert.rejects(chromeEndpoint(file),/valid local connection endpoint/);
    }
    await fs.writeFile(file,'9222\n/devtools/browser/1234-ABCD\n');
    assert.equal(await chromeEndpoint(file),'ws://127.0.0.1:9222/devtools/browser/1234-ABCD');
    const readFile=fs.readFile;
    try {
      fs.readFile=async()=>{throw Object.assign(new Error('Denied'),{code:'EPERM'});};
      await assert.rejects(chromeEndpoint(file),/macOS blocked Chrome connection access/);
    } finally {fs.readFile=readFile;}
    console.log('Chrome endpoint accepts only the default-profile loopback browser endpoint.');
  } finally {await fs.rm(directory,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});

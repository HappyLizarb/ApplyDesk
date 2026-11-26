const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {pauseWorkspace}=require('./control.cjs');
const {connectApprovedChrome}=require('./chrome-approval.cjs');

// Chrome publishes only its local connection endpoint after the user enables remote debugging.
async function chromeEndpoint(file=path.join(os.homedir(),'Library/Application Support/Google/Chrome/DevToolsActivePort')) {
  let contents;
  try {contents=await fs.readFile(file,'utf8');}
  catch(error) {
    if(['EPERM','EACCES'].includes(error.code))throw new Error('macOS blocked Chrome connection access. In System Settings > Privacy & Security > Files & Folders, allow Google Chrome access for ApplyDesk or the app that launched it, then reconnect.');
    throw new Error('Open Chrome and enable remote debugging at chrome://inspect/#remote-debugging, then reconnect ApplyDesk.');
  }
  const lines=contents.trim().split(/\r?\n/),port=Number(lines[0]);
  if(lines.length!==2||!/^\d+$/.test(lines[0])||!Number.isSafeInteger(port)||port<1||port>65535
    ||!/^\/devtools\/browser\/[a-z0-9-]+$/i.test(lines[1]))throw new Error('Chrome did not provide a valid local connection endpoint.');
  return `ws://127.0.0.1:${port}${lines[1]}`;
}

// Resolve only the exact target we just created; concurrent user tabs are never adopted.
async function createdPage(context,targetId,before) {
  for(let attempt=0;attempt<100;attempt++) {
    for(const page of context.pages().filter(page=>!before.has(page)&&!page.isClosed())) {
      const client=await context.newCDPSession(page);
      try {if((await client.send('Target.getTargetInfo')).targetInfo.targetId===targetId)return page;}
      finally {await client.detach();}
    }
    await new Promise(resolve=>setTimeout(resolve,30));
  }
  throw new Error('The dedicated application tab did not become available.');
}

// Window identity is checked before navigation or resizing, including after a user moves a tab.
async function applicationWindow(session,page) {
  if(!session.externalChrome||!session.ownedPages.has(page))throw new Error('This is not an ApplyDesk application tab.');
  const client=await session.context.newCDPSession(page);
  try {
    const {windowId}=await client.send('Browser.getWindowForTarget');
    if(session.windowId!==undefined&&windowId!==session.windowId)
      throw new Error('This application tab was moved out of its dedicated window. Move it back before continuing.');
    return windowId;
  } finally {await client.detach();}
}

// Start one new window; later tabs must land in that same verified window before any website opens.
async function createApplicationPage(session,newWindow=false) {
  const before=new Set(session.context.pages()),client=await session.browser.newBrowserCDPSession();
  let targetId,page;
  try {
    if(!newWindow) {
      const anchor=[...session.ownedPages].find(page=>!page.isClosed());
      if(!anchor)throw new Error('The dedicated application window is closed. Reconnect ApplyDesk.');
      await applicationWindow(session,anchor);await anchor.bringToFront();
    }
    ({targetId}=await client.send('Target.createTarget',{url:'about:blank',newWindow,background:false}));
    page=await createdPage(session.context,targetId,before);session.ownedPages.add(page);
    const windowId=await applicationWindow(session,page);
    if(newWindow)session.windowId=windowId;
    page.once('close',()=>session.ownedPages.delete(page));
    return page;
  } catch(error) {
    if(targetId)await client.send('Target.closeTarget',{targetId}).catch(()=>{});
    if(page)session.ownedPages.delete(page);
    throw error;
  } finally {await client.detach();}
}

// Attach with Chrome's permission prompt and preserve the user's browser defaults and profile.
async function connectExistingChrome(chromium,options,state,signal) {
  if(!options.applicationWorkspace||options.headless||options.source||options.research||options.applicationSource)
    throw new Error('Existing Chrome is available only for the visible application workspace.');
  const file=options.allowLocal?options.chromeActivePortFile:undefined;
  const endpoint=await chromeEndpoint(file);
  const browser=await connectApprovedChrome(chromium,options,endpoint,signal);
  const context=browser.contexts()[0];
  const session={browser,context,state,options,externalChrome:true,ownedPages:new Set(),tabs:new Map()};
  try {
    if(!context)throw new Error('Chrome has no signed-in profile available.');
    signal?.throwIfAborted();
    session.page=await createApplicationPage(session,true);
    signal?.throwIfAborted();
    return session;
  } catch(error) {await browser.close();throw error;}
}

// Disconnecting CDP releases automation only; the user's process and every visible tab remain open.
async function releaseSession(session) {
  if(session.releasePromise)return session.releasePromise;
  session.releasePromise=(async()=>{
    if(!session.externalChrome) {await session.context.close();return;}
    await pauseWorkspace(session);
    const {disposeOwnedPageGuards}=require('./guard.cjs');
    for(const [page,state] of session.state.pages) {
      if(!page.isClosed())await disposeOwnedPageGuards(page,state).catch(()=>{});
    }
    await session.browser.close();
  })();
  return session.releasePromise;
}
module.exports={chromeEndpoint,connectExistingChrome,createApplicationPage,applicationWindow,releaseSession};

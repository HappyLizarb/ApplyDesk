const {pauseWorkspace, revoke, checkpoint} = require('./control.cjs');
const {routeSocket,installOwnedPageGuards} = require('./guard.cjs');
const {importSource} = require('./source-storage.cjs');
const {createApplicationPage,applicationWindow}=require('./existing-chrome.cjs');

// Only popups opened by our application pages are tracked; personal Chrome tabs stay unrelated.
function trackApplicationPopups(session,page,owner) {
  page.on('popup',popup=>{
    owner.unexpectedPages ||= new Set();owner.unexpectedPages.add(popup);
    const state={normalNetworking:true,allowLocal:owner.allowLocal,uploadRoots:owner.uploadRoots,source:null,research:false,
      manual:false,paused:true,generation:0,controller:null,auth:null,submission:null,blocked:[],unexpectedPages:new Set(),
      ownedPages:session.state.pages,workspace:true,pace:owner.pace,phase:'reading',applicationSource:null};
    session.state.pages.set(popup,state);
    installOwnedPageGuards(popup,state).catch(()=>{});
    trackApplicationPopups(session,popup,owner);
    popup.once('close',()=>{owner.unexpectedPages.delete(popup);session.state.pages.delete(popup);});
  });
}

// Each retained job tab owns its own form state and temporary network permissions.
async function jobTab(session, jobId) {
  if (typeof jobId !== 'string' || !jobId || jobId.length > 200) throw new Error('An application job ID is required.');
  let tab = session.tabs.get(jobId);
  if (tab && !tab.page.isClosed()) return tab;
  const page = !session.tabs.size && !session.page.isClosed() ? session.page
    : session.externalChrome?await createApplicationPage(session):await session.context.newPage();
  const state = {...session.state, pages: undefined, blocked: [], auth: null, submission: null,
    manual: false, paused: true, generation: 0, applicationSource: null, workspace: true, ownedPages: session.state.pages,
    pace: !session.options.headless,unexpectedPages:new Set()};
  tab = {context: session.context, page, state, options: session.options, jobId,
    externalChrome:session.externalChrome,ownedPages:session.ownedPages,windowId:session.windowId,browser:session.browser};
  session.tabs.set(jobId, tab);
  session.state.pages.set(page, state);
  if(session.externalChrome) {
    await installOwnedPageGuards(page,state);trackApplicationPopups(session,page,state);
  } else await page.routeWebSocket('**/*', socket => routeSocket(socket, state));
  page.once('close', () => { session.state.pages.delete(page); if (session.tabs.get(jobId) === tab) session.tabs.delete(jobId); });
  return tab;
}

// A fresh authorized open resumes one tab only after all earlier Stop cleanup completes.
async function resumeTab(session, tab, params, check) {
  if (![null, undefined, 'linkedin', 'handshake'].includes(params.application_source)) throw new Error('Unknown application source.');
  await session.pausePromise;
  check();
  for (const other of session.tabs.values()) if (other !== tab) revoke(other.state);
  tab.state.controller?.abort(new Error('Application paused. Open it again to resume.'));
  tab.state.generation += 1;
  Object.assign(tab.state, {paused: false, manual: false, auth: null, submission: null,
    controller: new AbortController(), applicationSource: params.application_source || null, phase: 'reading', blocked: []});
  await tab.page.evaluate(() => {
    if (globalThis.__applyDesk) Object.assign(globalThis.__applyDesk, {paused: false, manual: false, authUntil: 0, finalUntil: 0, finalId: null});
  });
  check();
}

// Tab closure never closes another job or the workspace's persistent browser process.
async function closeJob(session, jobId) {
  const tab = session.tabs.get(jobId);
  if (!tab) return {closed: true};
  revoke(tab.state);
  const pages=session.externalChrome?[...session.ownedPages]:session.context.pages();
  if (pages.filter(page => !page.isClosed()).length === 1) {
    session.page = session.externalChrome?await createApplicationPage(session):await session.context.newPage();
    await session.page.goto('about:blank');
  }
  await tab.page.close();
  return {closed: true};
}

// The workspace routes only existing bounded browser commands, with explicit job ownership.
async function workspaceCommand(session, method, params, execute) {
  const generation = session.generation || 0;
  const check = () => { if ((session.generation || 0) !== generation) throw new Error('Application paused. Open it again to resume.'); };
  if (method === 'import_source') {
    if(session.externalChrome)throw new Error('Existing Chrome already uses its signed-in profile; sign-in import is unavailable.');
    return importSource(session, params);
  }
  if (method === 'close_job') return closeJob(session, params.job_id);
  if (method === 'pause') return pauseWorkspace(session, params.job_id);
  const requested = session.tabs.get(params.job_id);
  const existing = method === 'show' && (!requested || requested.page.isClosed())
    ? [...session.tabs.values()].reverse().find(tab => !tab.page.isClosed()) : requested;
  if (method !== 'open' && (!existing || existing.page.isClosed())) throw new Error('This application tab is closed.');
  const tab = method === 'open' ? await jobTab(session, params.job_id) : existing;
  check();
  if(session.externalChrome)await applicationWindow(session,tab.page);
  if (method === 'open') await resumeTab(session, tab, params, check);
  if (!['show', 'snapshot'].includes(method)) checkpoint(tab.state)();
  if (!['show', 'snapshot', 'open'].includes(method) && !session.options.headless) await tab.page.bringToFront();
  check();
  session.page = tab.page;
  return execute(tab, method, params);
}

module.exports = {workspaceCommand, pauseWorkspace};

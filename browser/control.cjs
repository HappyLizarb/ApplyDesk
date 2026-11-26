// A command keeps its own generation so Stop cannot be undone by a later resume.
function checkpoint(state) {
  const generation = state.generation || 0;
  const check = () => {
    if (state.paused || (state.generation || 0) !== generation) throw new Error('Application paused. Open it again to resume.');
  };
  check.signal = state.controller?.signal;
  return check;
}

// Revocation happens synchronously, before any asynchronous browser cleanup.
function revoke(state) {
  state.generation = (state.generation || 0) + 1;
  state.paused = true;
  state.controller?.abort(new Error('Application paused. Open it again to resume.'));
  state.manual = false;
  state.auth = null;
  state.submission = null;
  state.uploadPermit = null;
  for (const connection of state.sockets || []) {
    connection.socket.close().catch(() => {});connection.server.close().catch(() => {});
  }
  state.sockets?.clear();
}

// Revoke agent controls while normal employer reads and autosaves finish in the retained tab.
async function freezePage(context, page, normalNetworking = false) {
  if (page.isClosed()) return;
  let client;
  try {
    if (!normalNetworking) {
      client = await context.newCDPSession(page);
      await client.send('Page.stopLoading');
    }
    await page.evaluate(() => {
      if (!globalThis.__applyDesk) return;
      Object.assign(globalThis.__applyDesk, {manual: false, paused: true, authUntil: 0, finalUntil: 0, finalId: null});
    });
  } catch {} // A user-closed tab needs no further cancellation work.
  finally { if (client) await client.detach().catch(() => {}); }
}

// Signals can interrupt a fill batch without waiting behind its serialized request.
function pauseWorkspace(session, jobId) {
  session.generation = (session.generation || 0) + 1;
  const tabs = [...session.tabs.values()].filter(tab => !jobId || tab.jobId === jobId);
  for (const tab of tabs) revoke(tab.state);
  const previous = session.pausePromise || Promise.resolve();
  session.pausePromise = previous.then(() => Promise.all(tabs.map(tab => freezePage(session.context, tab.page, tab.state.normalNetworking))));
  return session.pausePromise.then(() => ({paused: true}));
}

module.exports = {checkpoint, revoke, freezePage, pauseWorkspace};

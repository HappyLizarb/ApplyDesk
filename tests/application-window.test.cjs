const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// A visible synthetic form records writes without contacting any employer or signed-in account.
async function fixture() {
  const writes = [];
  const server = http.createServer((request, response) => {
    if (request.method === 'POST') writes.push(request.url);
    response.end('<title>ApplyDesk visibility test</title><form method="POST" action="/submit"><label>Name<input name="name"></label><button>Submit application</button></form>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, writes, url: `http://127.0.0.1:${server.address().port}`};
}

// Restoring a minimized window preserves the exact page and leaves the agent in control.
async function visibilityChecks(session, url) {
  const page = session.page, snapshot = await dispatch(session, 'open', {url});
  const client = await session.context.newCDPSession(page);
  const {windowId} = await client.send('Browser.getWindowForTarget');
  await client.send('Browser.setWindowBounds', {windowId, bounds: {windowState: 'minimized'}});
  assert.deepEqual(await dispatch(session, 'show', {}), {visible: true});
  const result = await client.send('Browser.getWindowBounds', {windowId});
  assert.notEqual(result.bounds.windowState, 'minimized');
  assert.equal(session.page, page);
  assert.equal(session.context.pages().length, 1);
  assert.equal(session.state.manual, false);
  const field = snapshot.fields.find(item => item.name === 'name');
  const filled = await dispatch(session, 'fill', {snapshot_id: snapshot.snapshot_id, answers: [{field_id: field.field_id, value: 'Example Person'}]});
  assert.equal(filled.readbacks[0].value, 'Example Person');
  assert.equal(session.state.manual, false);
  session.state.research = true;
  await assert.rejects(dispatch(session, 'show', {}), /Web research supports page reads/);
  session.state.research = false;
  await client.detach();
}

// All browser state belongs to a disposable local profile and is closed after the check.
async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-window-'));
  const site = await fixture();
  let session;
  try {
    session = await start({dataRoot: root, headless: false, allowLocal: true});
    await visibilityChecks(session, site.url);
    assert.deepEqual(site.writes, []);
    console.log('Visible application window passed: existing page restored, filling remains visible, no manual handoff or submission.');
  } finally {
    if (session) await session.context.close();
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(root, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

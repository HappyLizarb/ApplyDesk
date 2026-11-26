const readline = require('node:readline');
const {pauseWorkspace} = require('./control.cjs');
const {releaseSession}=require('./existing-chrome.cjs');

// Interrupt messages bypass the command queue, including while Chrome is still starting.
function receive(protocol, line) {
  let request;
  try { request = JSON.parse(line); }
  catch (error) { return process.stdout.write(JSON.stringify({id: null, error: 'Invalid browser request.'}) + '\n'); }
  if (request.method === 'interrupt' && protocol.options.applicationWorkspace) {
    protocol.epoch += 1;
    cancelStartup(protocol);
    if (protocol.session) pauseWorkspace(protocol.session).catch(() => {});
    return;
  }
  const epoch = protocol.epoch;
  protocol.queue = protocol.queue.then(() => respond(protocol, request, epoch));
}

// A pending existing-Chrome connection must not approve its native prompt after Stop or shutdown.
function cancelStartup(protocol) {
  if(!protocol.options.existingChrome||protocol.session)return;
  protocol.startup.abort(new Error('Application paused. Open it again to resume.'));
}

// A queued request cannot undo a Stop that arrived after that request was received.
async function respond(protocol, request, epoch) {
  try {
    const session = await protocol.ready;
    if (epoch !== protocol.epoch) throw new Error('Application paused. Open it again to resume.');
    const result = await protocol.dispatch(session, request.method, request.params || {});
    process.stdout.write(JSON.stringify({id: request.id, result}) + '\n');
    if (request.method === 'close') protocol.input.close();
  } catch (error) {
    process.stdout.write(JSON.stringify({id: request?.id ?? null, error: protocol.safeError(error, request?.params)}) + '\n');
  }
}

// The listener is installed before startup so an early Stop never enables Node's signal debugger.
async function runProtocol(options, start, dispatch, safeError) {
  const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
  const protocol = {input, options, dispatch, safeError, epoch: 0, session: null, queue: Promise.resolve(),startup:new AbortController()};
  input.on('line', line => receive(protocol, line));
  const closed = new Promise(resolve => input.once('close',()=>{cancelStartup(protocol);resolve();}));
  protocol.ready = Promise.resolve().then(() => start(options,{signal:protocol.startup.signal})).then(session => {
    protocol.session = session;
    if (options.applicationWorkspace) session.context.once('close', () => input.close());
    return session;
  });
  protocol.ready.catch(error => { if(!protocol.startup.signal.aborted)process.stderr.write(safeError(error) + '\n');input.close(); });
  process.once('SIGTERM', () => {
    cancelStartup(protocol);
    protocol.ready.then(releaseSession).catch(()=>{}).finally(() => process.exit(0));
  });
  await closed;
  if (protocol.session && options.applicationWorkspace) {
    protocol.epoch += 1;
    await pauseWorkspace(protocol.session);
  }
  await protocol.queue;
  if(protocol.startup.signal.aborted)process.exit(0); // CDP's pending handshake has no AbortSignal API.
  const session = await protocol.ready;
  await releaseSession(session);
}

module.exports = {runProtocol};

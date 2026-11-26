const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');
const {spawn} = require('node:child_process');

// Test-only driver hooks arm a fictional disabled control and simulate the user closing Chrome.
const RUNNER = `const {start,dispatch}=require('./browser/worker.cjs');
const {runProtocol}=require('./browser/protocol.cjs');
runProtocol(JSON.parse(process.env.APPLYDESK_BROWSER_OPTIONS),start,async(session,method,params)=>{
  if(method==='test_arm_wait'){session.tabs.get('one').state.pace=true;return session.page.evaluate(()=>{
    const input=document.querySelector('input');
    setTimeout(()=>{input.disabled=true},80);setTimeout(()=>{input.disabled=false},1400);
  });}
  if(method==='test_close_window'){await session.context.close();return {closed:true};}
  return dispatch(session,method,params);
},error=>String(error.message)).catch(error=>{console.error(error);process.exitCode=1});`;

// Responses are matched by ID while interrupt deliberately has no response or queue slot.
function worker(root) {
  const process = spawn(global.process.execPath, ['-e', RUNNER], {cwd: path.join(__dirname, '..'),
    env: {...global.process.env, APPLYDESK_BROWSER_OPTIONS: JSON.stringify({dataRoot: root,
      applicationWorkspace: true, headless: true, allowLocal: true})}, stdio: ['pipe', 'pipe', 'pipe']});
  const waiting = new Map(), messages = [];let counter = 0, errors = '';
  readline.createInterface({input: process.stdout}).on('line', line => {
    const message = JSON.parse(line);messages.push(message);
    waiting.get(message.id)?.(message);waiting.delete(message.id);
  });
  process.stderr.on('data', chunk => { errors += chunk; });
  const exited = new Promise(resolve => process.once('exit', (code, signal) => resolve({code, signal})));
  const request = (method, params = {}) => {
    const id = ++counter, result = new Promise(resolve => waiting.set(id, resolve));
    process.stdin.write(JSON.stringify({id, method, params}) + '\n');return result;
  };
  const interrupt = () => process.stdin.write('{"method":"interrupt"}\n');
  return {process, request, interrupt, exited, messages, errors: () => errors};
}

// All traffic stays on localhost and the fixture records attempted navigation and writes.
async function fixture() {
  const visits = [], writes = [];
  const server = http.createServer((request, response) => {
    visits.push(request.url);if (request.method === 'POST') writes.push(request.url);
    response.setHeader('Content-Type', 'text/html');
    response.end('<title>Protocol fixture</title><label>Name<input name="name" value="initial"></label>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, visits, writes, url: `http://127.0.0.1:${server.address().port}`};
}

// Buffered startup commands are invalidated before the browser exists, then a new open can resume.
async function startupChecks(child, site) {
  const canceled = child.request('open', {job_id: 'one', url: site.url + '/canceled-startup'});
  child.interrupt();
  assert.match((await canceled).error, /paused/);
  assert.equal(site.visits.includes('/canceled-startup'), false);
  const opened = await child.request('open', {job_id: 'one', url: site.url + '/active'});
  assert.ok(opened.result.snapshot_id);
}

// Stop cancels an in-flight auto-wait and queued opens without closing or changing the live tab.
async function inFlightChecks(child, site) {
  const before = (await child.request('snapshot', {job_id: 'one'})).result;
  await child.request('test_arm_wait');
  const filling = child.request('fill', {job_id: 'one', snapshot_id: before.snapshot_id,
    answers: [{field_id: before.fields[0].field_id, value: 'must-not-write'}]});
  const queued = child.request('open', {job_id: 'two', url: site.url + '/canceled-queued'});
  await new Promise(resolve => setTimeout(resolve, 500));child.interrupt();
  assert.match((await filling).error, /paused|abort/i);
  assert.match((await queued).error, /paused/);
  await new Promise(resolve => setTimeout(resolve, 1100));
  const after = (await child.request('snapshot', {job_id: 'one'})).result;
  assert.equal(after.fields[0].value, 'initial');assert.equal(after.url, before.url);
  assert.equal(site.visits.includes('/canceled-queued'), false);assert.deepEqual(site.writes, []);
  const resumed = await child.request('open', {job_id: 'one', url: site.url + '/resumed'});
  assert.ok(resumed.result.snapshot_id);
}

// Closing the native window exits the worker even when its stdin owner remains alive.
async function closeChecks(child) {
  const closed = child.request('test_close_window');
  assert.deepEqual((await closed).result, {closed: true});
  const exit = await child.exited;
  assert.equal(exit.code, 0);assert.equal(child.errors(), '');
  assert.equal(child.messages.some(message => message.id === undefined), false);
}

async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-protocol-')), site = await fixture();
  const child = worker(root), timeout = setTimeout(() => child.process.kill('SIGKILL'), 20000);
  try {
    await startupChecks(child, site);await inFlightChecks(child, site);await closeChecks(child);
    console.log('Application protocol passed: startup, queued and in-flight interruption, explicit resume, native-window close exit.');
  } finally {
    clearTimeout(timeout);child.process.kill('SIGKILL');
    await child.exited;await new Promise(resolve => site.server.close(resolve));
    await fs.rm(root, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
  }
}

main().catch(error => { console.error(error);process.exitCode = 1; });

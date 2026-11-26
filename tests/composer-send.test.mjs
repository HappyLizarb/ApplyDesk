import assert from 'node:assert/strict';
import {test, beforeEach, afterEach} from 'node:test';
import {state, toast} from '../web/ui.js';
import {composerIsWorking, updateComposeAction, submitComposer} from '../web/composer-send.js';

let elements, calls, serverState, respond;

// Minimal elements expose the same public properties the composer uses in a browser.
function element(value='') {
  return {
    value, disabled:false, hidden:true, textContent:'', innerHTML:'', attributes:{},
    classList:{toggle() {}},
    setAttribute(name, content) {this.attributes[name]=content;},
  };
}

// Each test begins connected and idle, with independent browser and HTTP state.
beforeEach(() => {
  elements=Object.fromEntries(['send-assistant','assistant-input','job-count','profile-indicator','toast']
    .map(id=>['#'+id,element()]));
  state.data={jobs:[],profile:{confirmed:false},orchestrator:{
    paused:false,active:null,messages:[],account:{connected:true},
  }};
  serverState=structuredClone(state.data);calls=[];
  globalThis.document={querySelector:selector=>elements[selector]};
  globalThis.window={dispatchEvent() {}};
  respond=async path=>({ok:true,data:path==='/api/state'?serverState:{}});
  globalThis.fetch=async (path, options={})=>{
    const body=options.body?JSON.parse(options.body):undefined;
    calls.push({path,body});
    const result=await respond(path,body);
    return {ok:result.ok,json:async()=>result.data};
  };
  updateComposeAction();
});

afterEach(() => clearTimeout(toast.timer));

// A successful send clears only text acknowledged by the server.
test('accepted message is posted once and cleared', async () => {
  elements['#assistant-input'].value='  Hello  ';
  await submitComposer();
  assert.deepEqual(calls,[{path:'/api/orchestrator/send',body:{message:'Hello'}},
    {path:'/api/state',body:undefined}]);
  assert.equal(elements['#assistant-input'].value,'');
  assert.equal(elements['#send-assistant'].disabled,true);
});

// A failed request leaves the draft editable and displays the provider failure.
test('send failure preserves draft and re-enables sending', async () => {
  elements['#assistant-input'].value='Keep this text';
  respond=async()=>({ok:false,data:{error:'Connection failed'}});
  await submitComposer();
  assert.equal(elements['#assistant-input'].value,'Keep this text');
  assert.equal(elements['#toast'].textContent,'Connection failed');
  assert.equal(elements['#send-assistant'].disabled,false);
  assert.equal(calls.length,1);
});

// Stop must remain available when active work has no current input or model stream.
test('active tool execution stops with an empty composer', async () => {
  state.data.orchestrator.active={task_id:'draft-1'};
  serverState.orchestrator={...state.data.orchestrator,paused:true};
  updateComposeAction();
  assert.equal(elements['#send-assistant'].attributes['aria-label'],'Stop agent');
  assert.match(elements['#send-assistant'].innerHTML,/#icon-stop/);
  assert.equal(elements['#send-assistant'].disabled,false);
  await submitComposer();
  assert.deepEqual(calls[0],{path:'/api/orchestrator/control',body:{paused:true}});
  assert.equal(elements['#send-assistant'].disabled,true);
  assert.equal(elements['#send-assistant'].attributes['aria-label'],'Stopping agent');
});

// Failed resume keeps a retry action, without posting the accepted message twice.
test('failed resume retries only control, not the accepted message', async () => {
  state.data.orchestrator.paused=true;
  elements['#assistant-input'].value='Start a new turn';
  respond=async path=>path==='/api/orchestrator/control'
    ? {ok:false,data:{error:'Resume failed'}} : {ok:true,data:{}};
  await submitComposer();
  assert.equal(elements['#assistant-input'].value,'');
  assert.equal(elements['#send-assistant'].attributes['aria-label'],'Resume queued message');
  assert.equal(elements['#send-assistant'].disabled,false);
  assert.match(elements['#toast'].textContent,/Message queued/);
  respond=async path=>({ok:true,data:path==='/api/state'?serverState:{}});
  await submitComposer();
  assert.equal(calls.filter(call=>call.path==='/api/orchestrator/send').length,1);
  assert.equal(calls.filter(call=>call.path==='/api/orchestrator/control').length,2);
  assert.equal(elements['#send-assistant'].attributes['aria-label'],'Send message');
});

// Cancellation cannot be undone by a second click while the worker is winding down.
test('paused active work disables the button and ignores submission', async () => {
  Object.assign(state.data.orchestrator,{paused:true,active:{task_id:'draft-1'}});
  elements['#assistant-input'].value='Wait for cancellation';
  updateComposeAction();
  assert.equal(elements['#send-assistant'].disabled,true);
  assert.equal(elements['#send-assistant'].attributes['aria-busy'],'true');
  await submitComposer();
  assert.deepEqual(calls,[]);
  assert.equal(elements['#assistant-input'].value,'Wait for cancellation');
});

// The queue counts as work even before the scheduler opens a model connection.
test('queued user messages enable stopping but paused and completed messages do not', async () => {
  const engine=state.data.orchestrator;
  engine.messages=[{role:'user',status:'queued'}];
  assert.equal(composerIsWorking(engine),true);
  updateComposeAction();
  assert.equal(elements['#send-assistant'].disabled,false);
  await submitComposer();
  assert.deepEqual(calls[0],{path:'/api/orchestrator/control',body:{paused:true}});
  engine.paused=true;
  assert.equal(composerIsWorking(engine),false);
  engine.paused=false;engine.messages=[{role:'user',status:'complete'}];
  assert.equal(composerIsWorking(engine),false);
  engine.messages=[{role:'assistant',status:'running'}];
  assert.equal(composerIsWorking(engine),false);
});

// Draft edits made during a request belong to the next turn and must survive.
test('newly typed text is preserved while a send is pending', async () => {
  elements['#assistant-input'].value='First message';
  let acknowledge;
  respond=path=>path==='/api/orchestrator/send'
    ? new Promise(resolve=>{acknowledge=resolve;})
    : Promise.resolve({ok:true,data:serverState});
  const request=submitComposer();
  assert.equal(elements['#send-assistant'].disabled,true);
  elements['#assistant-input'].value='Second message';
  await submitComposer();
  acknowledge({ok:true,data:{}});
  await request;
  assert.equal(elements['#assistant-input'].value,'Second message');
  assert.equal(calls.filter(call=>call.path==='/api/orchestrator/send').length,1);
});

// A disconnected account cannot enqueue another message from keyboard submission.
test('disconnected accounts cannot send', async () => {
  state.data.orchestrator.account.connected=false;
  elements['#assistant-input'].value='Wait for connection';
  updateComposeAction();
  assert.equal(elements['#send-assistant'].disabled,true);
  await submitComposer();
  assert.deepEqual(calls,[]);
});

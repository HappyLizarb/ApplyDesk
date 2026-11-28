import assert from 'node:assert/strict';
import {test,beforeEach,afterEach} from 'node:test';
import {state,toast} from '../web/ui.js';
import {apiProviders,bindApiSettings,updateApiSettings} from '../web/agent-settings.js';

let root,cards,elements,calls,serverState,respond;

// Minimal browser elements keep these checks on public event handlers and HTTP requests.
function element(value='') {
  return {value,disabled:false,hidden:false,textContent:'',classList:{toggle() {}}};
}

// Cards keep separate controls so accidental cross-provider reads are observable.
function providerCard(provider) {
  const controls=Object.fromEntries(['input','select','[type=submit]','[data-remove-key]','[data-key-status]']
    .map(selector=>[selector,element()]));
  return {dataset:{provider},querySelector:selector=>controls[selector],querySelectorAll:()=>[],remove(){cards.splice(cards.indexOf(this),1);}};
}

// No credentials or real network calls are used by the fixture.
beforeEach(() => {
  cards=Object.keys(apiProviders).filter(id=>id!=='cursor').map(providerCard);
  elements=Object.fromEntries(['connection-source','connection-status','connection-error','api-model-refresh',
    'add-api-key','api-key-rows','job-count','profile-indicator','toast'].map(id=>['#'+id,element()]));
  root={querySelector:selector=>elements[selector],querySelectorAll:()=>cards};
  state.data={jobs:[],profile:{confirmed:false},tasks:[],orchestrator:{account:{
    source:'chatgpt',connected:true,api_keys_saved:{openai:true,anthropic:false,google:true},error:'',
  }}};
  serverState=structuredClone(state.data);calls=[];
  globalThis.document={querySelector:selector=>elements[selector]};
  globalThis.window={dispatchEvent() {}};
  respond=async path=>({ok:true,data:path==='/api/state'?serverState:{}});
  globalThis.fetch=async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):undefined;
    calls.push({path,body});
    const result=await respond(path,body);
    return {ok:result.ok,json:async()=>result.data};
  };
  bindApiSettings(root);updateApiSettings(root);
});

afterEach(() => clearTimeout(toast.timer));

// Form handlers deliberately return synchronously; wait for their visible disabled state to clear.
async function settled() {
  const deadline=Date.now()+3000;
  while(elements['#connection-source'].disabled) {
    if(Date.now()>deadline)throw new Error('Connection controls did not recover');
    await new Promise(resolve=>setTimeout(resolve,5));
  }
}

test('selecting each provider posts only that source and restores the server choice',async()=>{
  for(const source of ['xai_api','anthropic_api','google_api','openai_api','chatgpt']) {
    serverState.orchestrator.account.source=source;
    await elements['#connection-source'].onchange({target:{value:source}});
    assert.deepEqual(calls.at(-2),{path:'/api/orchestrator/source',body:{source}});
    assert.equal(elements['#connection-source'].value,source);
    assert.equal(elements['#api-model-refresh'].hidden,source==='chatgpt');
    assert.equal(elements['#connection-status'].textContent,'');
  }
});

test('removal names one provider without switching the selected source',async()=>{
  state.data.orchestrator.account.source='google_api';
  serverState=structuredClone(state.data);
  serverState.orchestrator.account.api_keys_saved.openai=false;
  await cards[0].querySelector('[data-remove-key]').onclick();
  assert.deepEqual(calls[0],{path:'/api/orchestrator/api-key/remove',body:{provider:'openai'}});
  assert.equal(elements['#connection-source'].value,'google_api');
  assert.equal(cards.some(card=>card.dataset.provider==='openai'),false);
  assert.equal(cards.find(card=>card.dataset.provider==='google').querySelector('[data-key-status]').textContent,'Key saved');
});

test('every save clears only its password and posts the matching provider',async()=>{
  for(const card of cards)card.querySelector('input').value=`test-${card.dataset.provider}`;
  respond=async(path,body)=>{
    if(path==='/api/orchestrator/api-key') {
      serverState.tasks=[{id:'key-check',status:'done',result:{}}];
      serverState.orchestrator.account.source=body.provider+'_api';
      serverState.orchestrator.account.api_keys_saved[body.provider]=true;
      return {ok:true,data:{task:'key-check'}};
    }
    return {ok:true,data:serverState};
  };
  for(const [index,card] of cards.entries()) {
    const provider=card.dataset.provider;
    card.onsubmit({preventDefault() {}});
    assert.equal(card.querySelector('input').value,'');
    assert.deepEqual(calls.at(-1),{path:'/api/orchestrator/api-key',body:{key:`test-${provider}`,provider}});
    assert.equal(cards.some(item=>!item.querySelector('input').disabled),false);
    for(const next of cards.slice(index+1))assert.equal(next.querySelector('input').value,`test-${next.dataset.provider}`);
    await settled();
    assert.equal(elements['#connection-source'].value,provider+'_api');
    assert.equal(card.querySelector('[data-key-status]').textContent,'Key saved');
    assert.equal(card.querySelector('input').disabled,false);
  }
});

test('failed key validation keeps the key cleared and preserves prior connection',async()=>{
  const card=cards[1];card.querySelector('input').value='test-invalid-key';
  respond=async()=>({ok:false,data:{error:'Provider rejected this key'}});
  card.onsubmit({preventDefault() {}});
  assert.equal(card.querySelector('input').value,'');
  await settled();
  assert.equal(elements['#toast'].textContent,'Provider rejected this key');
  assert.equal(elements['#connection-source'].value,'chatgpt');
  assert.equal(card.querySelector('[data-key-status]').textContent,'No key saved');
  assert.equal(card.querySelector('input').disabled,false);
  assert.equal(calls.length,1);
});

test('pending source changes prevent competing saves and removals',async()=>{
  let release;
  respond=path=>path==='/api/orchestrator/source'
    ?new Promise(resolve=>{release=resolve;}):Promise.resolve({ok:true,data:serverState});
  const change=elements['#connection-source'].onchange({target:{value:'google_api'}});
  cards[1].querySelector('input').value='unsent-test-key';
  cards[1].onsubmit({preventDefault() {}});
  await cards[0].querySelector('[data-remove-key]').onclick();
  assert.equal(calls.length,1);
  assert.equal(cards[1].querySelector('input').value,'unsent-test-key');
  release({ok:true,data:{}});await change;
  assert.equal(elements['#connection-source'].disabled,false);
});

test('polling preserves password drafts and does not expose them in status text',()=>{
  for(const card of cards)card.querySelector('input').value='draft-'+card.dataset.provider;
  state.data.orchestrator.account.source='anthropic_api';
  state.data.orchestrator.account.connected=false;
  state.data.orchestrator.account.error='Connection needs attention';
  updateApiSettings(root);
  assert.equal(elements['#connection-status'].textContent,'Add your Anthropic API key below');
  assert.equal(elements['#connection-error'].textContent,'Connection needs attention');
  for(const card of cards) {
    assert.equal(card.querySelector('input').value,'draft-'+card.dataset.provider);
    assert.doesNotMatch(card.querySelector('[data-key-status]').textContent,/draft-/);
  }
  assert.equal(elements['#api-model-refresh'].hidden,true);
});

test('failed source changes roll back selection without altering saved key flags',async()=>{
  respond=async()=>({ok:false,data:{error:'Connection change failed'}});
  await elements['#connection-source'].onchange({target:{value:'google_api'}});
  assert.equal(elements['#connection-source'].value,'chatgpt');
  assert.equal(elements['#toast'].textContent,'Connection change failed');
  assert.deepEqual(state.data.orchestrator.account.api_keys_saved,{openai:true,anthropic:false,google:true});
  assert.equal(elements['#connection-source'].disabled,false);
});

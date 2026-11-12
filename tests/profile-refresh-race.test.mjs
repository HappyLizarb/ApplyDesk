import assert from 'node:assert/strict';
import {test,beforeEach} from 'node:test';
import {state,refresh} from '../web/ui.js';

const before={jobs:[],profile:{revision:7,confirmed:false,languages:[]}};
const saved={jobs:[],profile:{revision:8,confirmed:false,
  languages:[{name:'French',proficiency:'Professional working proficiency'}]}};
let requests,events;

// Deferred requests model independently completing browser polls without timers or network calls.
beforeEach(()=>{
  requests=[];events=[];
  const elements=new Map();
  state.data=structuredClone(before);
  globalThis.document={querySelector:selector=>{
    if(!elements.has(selector))elements.set(selector,{textContent:'',classList:{toggle(){}}});
    return elements.get(selector);
  }};
  globalThis.window={dispatchEvent:event=>events.push(event.type)};
  globalThis.fetch=()=>new Promise((resolve,reject)=>requests.push({reject,
    reply:value=>resolve({ok:true,json:async()=>value})}));
});

// Delayed HTTP snapshots reproduce a pre-save poll arriving after the completed save refresh.
test('a late pre-save poll cannot replace the newly saved profile',async()=>{
  const previousPoll=refresh();
  const afterSaveRefresh=refresh();
  requests[1].reply(structuredClone(saved));
  await afterSaveRefresh;
  assert.deepEqual(state.data.profile,saved.profile);
  requests[0].reply(structuredClone(before));
  assert.equal(await previousPoll,state.data);
  assert.deepEqual(state.data.profile,saved.profile,
    'The older poll erased the newest saved fields from shared state.');
  assert.deepEqual(events,['workspace-updated']);
});

// Saving directly installs the canonical profile even when no later refresh has finished yet.
test('an in-flight poll preserves the canonical POST profile and updates other state',async()=>{
  const pending=refresh(),canonical=structuredClone(saved.profile);
  state.data.profile=canonical;
  requests[0].reply({...structuredClone(before),jobs:[{id:'new-job'}]});
  const result=await pending;
  assert.equal(result.profile,canonical);
  assert.deepEqual(result.jobs,[{id:'new-job'}]);
  assert.deepEqual(events,['workspace-updated']);
});

// A pending newer request must not prevent the earlier successful response from being useful.
test('an earlier response applies while a newer refresh remains pending',async()=>{
  const earlier=refresh(),later=refresh();
  requests[0].reply(structuredClone(saved));
  await earlier;
  assert.deepEqual(state.data,saved);
  const latest={...structuredClone(saved),jobs:[{id:'later-job'}]};
  requests[1].reply(latest);
  await later;
  assert.deepEqual(state.data,latest);
  assert.equal(events.length,2);
});

// Request ordering protects all workspace state, even if the profile revision never changes.
test('late older replies cannot regress jobs with the same profile revision',async()=>{
  const older=refresh(),newer=refresh();
  const latest={...structuredClone(before),jobs:[{id:'latest-job'}]};
  requests[1].reply(latest);
  await newer;
  requests[0].reply(structuredClone(before));
  await older;
  assert.deepEqual(state.data,latest);
  assert.deepEqual(events,['workspace-updated']);
});

// Failed requests do not supersede a still-useful successful response.
test('a failed newer request does not discard an earlier valid response',async()=>{
  const earlier=refresh(),later=refresh();
  requests[1].reject(new Error('Connection interrupted'));
  await assert.rejects(later,/Connection interrupted/);
  requests[0].reply(structuredClone(saved));
  await earlier;
  assert.deepEqual(state.data,saved);
});

// Invalid snapshots never replace the last working state or publish update events.
test('malformed workspace responses leave the current profile untouched',async()=>{
  const invalid=[null,[],{jobs:[],profile:null},{jobs:{},profile:before.profile},
    {jobs:[],profile:[]},{jobs:[],profile:{revision:8}},
    ...[-1,1.5,'8',Number.MAX_SAFE_INTEGER+1].map(revision=>({jobs:[],profile:{revision,confirmed:false}}))];
  for(const payload of invalid){
    const current=state.data,pending=refresh();
    requests.at(-1).reply(payload);
    await assert.rejects(pending,/invalid state/);
    assert.equal(state.data,current);
  }
  assert.deepEqual(events,[]);
});

// A response already superseded by valid state is irrelevant, including an obsolete malformed body.
test('a superseded malformed response resolves to the current state',async()=>{
  const older=refresh(),newer=refresh();
  requests[1].reply(structuredClone(saved));
  await newer;
  requests[0].reply(null);
  assert.equal(await older,state.data);
  assert.deepEqual(state.data,saved);
});

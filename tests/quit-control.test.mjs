// Run with: node --experimental-vm-modules --test tests/quit-control.test.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test,beforeEach} from 'node:test';
import {createContext,SourceTextModule,SyntheticModule} from 'node:vm';

const source=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
let button,label,icon,listeners,calls,closed,errors,shutdown;

// A small DOM stand-in rejects replacements that would destroy the existing icon.
function makeButton() {
  label={textContent:'Quit'};icon={};
  return {
    disabled:false,title:'',attributes:{'aria-expanded':'false'},
    setAttribute(name,value) {this.attributes[name]=value;},
    getAttribute(name) {return this.attributes[name];},
    querySelector() {return label;},
    contains(target) {return [this,label,icon].includes(target);},
    classList:{toggle() {}},
    set innerHTML(value) {throw new Error('Replacing icon markup');},
    set textContent(value) {throw new Error('Replacing icon text');},
  };
}

// Load the real app module with inert page rendering and no live service calls.
beforeEach(async()=>{
  button=makeButton();listeners={};calls=[];closed=0;errors=[];
  shutdown=async()=>({});
  const context=createContext({document:{querySelector:()=>button,
    addEventListener:(name,handler)=>listeners[name]=handler},window:{close:()=>closed++}});
  const dependencies={state:{},refresh:()=>new Promise(()=>{}),
    toast:message=>errors.push(message),api:async(path,data)=>{
      calls.push({path,data});return shutdown();
    },renderApplications(){},renderProfile(){},renderDocuments(){},
    renderAgentSettings(){},updateAccount(){},initializeAssistant(){},toggleAssistant(){}};
  const module=new SourceTextModule(source,{context});
  await module.link(()=>new SyntheticModule(Object.keys(dependencies),function(){
    for(const [name,value] of Object.entries(dependencies))this.setExport(name,value);
  },{context}));
  await module.evaluate();
});

// Confirmation is visible before any shutdown request can be sent.
test('first click expands Quit without a service call',async()=>{
  await button.onclick();
  assert.equal(label.textContent,'Quit ApplyDesk');
  assert.equal(button.attributes['aria-expanded'],'true');
  assert.deepEqual(calls,[]);
  assert.equal(closed,0);
});

// The confirmed click reuses the existing authenticated shutdown endpoint.
test('second click shuts down once and closes the window',async()=>{
  await button.onclick();
  await button.onclick();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'shutdown');
  assert.equal(closed,1);
  assert.equal(button.disabled,true);
});

// Both pointer dismissal and keyboard dismissal require a fresh two-click sequence.
test('outside click and Escape collapse the confirmation',async()=>{
  await button.onclick();
  listeners.click({target:icon});
  assert.equal(button.attributes['aria-expanded'],'true');
  listeners.click({target:{}});
  assert.equal(label.textContent,'Quit');
  await button.onclick();
  listeners.keydown({key:'Escape'});
  assert.equal(label.textContent,'Quit');
  assert.equal(button.attributes['aria-expanded'],'false');
  assert.deepEqual(calls,[]);
});

// Failed shutdown keeps its confirmation and icon intact for an explicit retry.
test('failed shutdown restores the confirmed enabled state',async()=>{
  shutdown=async()=>{throw new Error('Service unavailable');};
  await button.onclick();
  await button.onclick();
  assert.equal(button.disabled,false);
  assert.equal(button.attributes['aria-busy'],'false');
  assert.equal(button.attributes['aria-expanded'],'true');
  assert.equal(label.textContent,'Quit ApplyDesk');
  assert.deepEqual(errors,['Service unavailable']);
  assert.equal(closed,0);
  shutdown=async()=>({});
  await button.onclick();
  assert.equal(calls.length,2);
  assert.equal(closed,1);
});

// Repeated input during shutdown cannot duplicate requests or reset confirmation.
test('pending shutdown ignores extra clicks and dismissal',async()=>{
  let finish;
  shutdown=()=>new Promise(resolve=>{finish=resolve;});
  await button.onclick();
  const pending=button.onclick();
  await button.onclick();
  listeners.click({target:{}});listeners.keydown({key:'Escape'});
  assert.equal(calls.length,1);
  assert.equal(button.attributes['aria-expanded'],'true');
  assert.equal(button.attributes['aria-busy'],'true');
  finish({});await pending;
  assert.equal(closed,1);
});

// Static initial markup agrees with the runtime's unconfirmed control state.
test('initial markup contains Quit and retains the existing close icon',async()=>{
  const html=await readFile(new URL('../web/index.html',import.meta.url),'utf8');
  const markup=html.match(/<button[^>]*id="quit-app"[\s\S]*?<\/button>/)[0];
  assert.match(markup,/aria-expanded="false"/);
  assert.match(markup,/href="#icon-close"/);
  assert.match(markup,/<span data-quit-label>Quit<\/span>/);
});

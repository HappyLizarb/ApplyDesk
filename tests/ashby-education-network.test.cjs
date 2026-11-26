const assert=require('node:assert/strict');
const {educationRequest,educationSchoolRequest,SCHOOL_QUERY}=require('../browser/ashby-education-network.cjs');
const pageUrl='https://jobs.ashbyhq.com/example/job/application';
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};
const state={generation:7,ashbySchoolSearch:{name:'Example University',pageUrl,generation:7,expires:Date.now()+30000}};
const body={operationName:'ApiSearchSchoolByCanonicalName',query:SCHOOL_QUERY,
 variables:{name:'Example University',organizationHostedJobsPageName:'example'}};
const request=change=>({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql',frame:()=>frame,method:()=> 'POST',postDataJSON:()=>body,...change});
const capturedQuery=require('node:fs').readFileSync(__dirname+'/fixtures/ashby-school-query.graphql','utf8');
assert.notEqual(capturedQuery.replace(/\s/g,''),SCHOOL_QUERY.replace(/\s/g,''));
assert(educationSchoolRequest(request({postDataJSON:()=>({...body,query:capturedQuery})}),state));
assert(educationSchoolRequest(request(),state));
assert(educationRequest(request(),state));
assert(educationSchoolRequest(request({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSearchSchoolByCanonicalName'}),state));
for(const query of ['?op=Other','?op=ApiSearchSchoolByCanonicalName&op=ApiSearchSchoolByCanonicalName','?op=ApiSearchSchoolByCanonicalName&submit=true','?other=ApiSearchSchoolByCanonicalName']) {
 assert.equal(educationSchoolRequest(request({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql'+query}),state),false);
}
assert.equal(educationSchoolRequest(request(),{...state,generation:8}),false);
assert.equal(educationSchoolRequest(request(),{...state,paused:true}),false);
for(const changed of [{operationName:'SubmitApplication'},{query:SCHOOL_QUERY+' mutation Submit {}'},
 {variables:{...body.variables,name:'Unconfirmed school'}},{variables:{...body.variables,organizationHostedJobsPageName:'other'}},
 {variables:{...body.variables,submit:true}}])assert.equal(educationSchoolRequest(request({postDataJSON:()=>({...body,...changed})}),state),false);
assert.equal(educationSchoolRequest(request({method:()=> 'PUT'}),state),false);
assert.equal(educationSchoolRequest(request({url:()=> 'https://other.example/api/non-user-graphql'}),state),false);
assert.equal(educationSchoolRequest(request(),{...state,ashbySchoolSearch:{...state.ashbySchoolSearch,expires:0}}),false);
console.log('Education lookup permissions bind the exact official query, confirmed name, employer and current page.');

// Independently render the AST extracted from the official bundle, including both complete definitions.
function renderOfficial(node) {
 switch(node.kind) {
  case 'Document':return node.definitions.map(renderOfficial).join('\n\n');
  case 'OperationDefinition':return node.operation+' '+renderOfficial(node.name)+'('+node.variableDefinitions.map(renderOfficial).join(', ')+') '+renderOfficial(node.selectionSet);
  case 'VariableDefinition':return renderOfficial(node.variable)+': '+renderOfficial(node.type);
  case 'Variable':return '$'+renderOfficial(node.name);
  case 'Name':return node.value;
  case 'NamedType':return renderOfficial(node.name);
  case 'NonNullType':return renderOfficial(node.type)+'!';
  case 'SelectionSet':return '{ '+node.selections.map(renderOfficial).join(' ')+' }';
  case 'Field':return (node.alias?renderOfficial(node.alias)+': ':'')+renderOfficial(node.name)
    +(node.arguments?.length?'('+node.arguments.map(renderOfficial).join(', ')+')':'')
    +(node.selectionSet?' '+renderOfficial(node.selectionSet):'');
  case 'Argument':return renderOfficial(node.name)+': '+renderOfficial(node.value);
  case 'FragmentSpread':return '...'+renderOfficial(node.name);
  case 'FragmentDefinition':return 'fragment '+renderOfficial(node.name)+' on '+renderOfficial(node.typeCondition)+' '+renderOfficial(node.selectionSet);
  default:throw new Error('Unexpected AST node: '+node.kind);
 }
}
const official=require('./fixtures/ashby-school-query-ast.json');
assert.equal(official.definitions.length,2);
assert.equal(renderOfficial(official).replace(/[\s,]/g,''),SCHOOL_QUERY.replace(/[\s,]/g,''));
console.log('School lookup query matches the complete official AST; routing parameters and generation revocation are checked.');

// The lookup captures its current generation and refuses a response after Stop/reopen.
(async()=>{
 const {EventEmitter}=require('node:events');
 const {findEducationSchool}=require('../browser/ashby-education-network.cjs');
 for(const interrupted of [false,true]) {
  const page=new EventEmitter(),lookup={generation:9};page.url=()=>pageUrl;
  page.waitForTimeout=()=>new Promise(resolve=>setImmediate(resolve));
  const input={fill:async()=>{if(interrupted)lookup.generation++;
   page.emit('response',{request:()=>request({url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSearchSchoolByCanonicalName'}),
    ok:()=>true,json:async()=>({data:{result:[{id:'canonical-id',name:'Example University'}]}})});
  }};
  const check=()=>{if(lookup.generation!==9)throw new Error('Stopped generation');};
  const result=findEducationSchool(page,lookup,input,'Example University',check);
  if(interrupted)await assert.rejects(result,/Stopped generation/);
  else assert.equal((await result).id,'canonical-id');
  assert.equal(lookup.ashbySchoolSearch.generation,9);
 }
 console.log('Live lookup setup captures generation and ignores a response from a stopped attempt.');
 await testCachedSchool();
})().catch(error=>{console.error(error);process.exitCode=1});

// The official widget is cache-first, so a second degree at the same school may produce no request.
async function testCachedSchool() {
 const {EventEmitter}=require('node:events'),{findEducationSchool}=require('../browser/ashby-education-network.cjs');
 const page=new EventEmitter(),lookup={generation:10};page.url=()=>pageUrl;
 page.waitForTimeout=async()=>{throw new Error('A fresh lookup was required');};
 let fills=0;
 const input={fill:async()=>{fills++;if(fills===1)page.emit('response',{request:()=>request(),ok:()=>true,
  json:async()=>({data:{result:[{id:'verified-school',name:'Example University'}]}})});}};
 const first=await findEducationSchool(page,lookup,input,'Example University',()=>{});
 first.id='caller-changed';
 assert.equal((await findEducationSchool(page,lookup,input,'Example University',()=>{})).id,'verified-school');
 assert.equal(fills,2);
 lookup.generation++;
 await assert.rejects(findEducationSchool(page,lookup,input,'Example University',()=>{}),/fresh lookup/);
 lookup.generation--;
 page.url=()=>pageUrl+'?changed=true';
 await assert.rejects(findEducationSchool(page,lookup,input,'Example University',()=>{}),/fresh lookup/);
 console.log('Repeated-school lookup reuses only its verified exact result on the same page and generation.');
}

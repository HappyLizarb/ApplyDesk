const {armAshbyAnswer,waitForAshbySave}=require('./ashby-drafts.cjs');
const SCHOOL_QUERY=`query ApiSearchSchoolByCanonicalName($name: String!, $organizationHostedJobsPageName: String!) {
 result: searchCanonicalSchools(name: $name, organizationHostedJobsPageName: $organizationHostedJobsPageName) {
 id ...AutocompleteCanonicalSchoolResultParts __typename }
}
fragment AutocompleteCanonicalSchoolResultParts on CanonicalSchool { id name domain country __typename }`;

// This fixed, literal-free query treats GraphQL whitespace and commas as insignificant.
function educationSchoolRequest(request,state) {
  try {
    const search=state.ashbySchoolSearch,url=new URL(request.url()),frame=request.frame(),page=new URL(frame.url());
    const body=request.postDataJSON(),values=body.variables,parameters=[...url.searchParams];
    const routing=!url.search||parameters.length===1&&parameters[0][0]==='op'&&parameters[0][1]==='ApiSearchSchoolByCanonicalName';
    return !!search&&!state.paused&&search.generation===state.generation&&Date.now()<search.expires&&page.href===search.pageUrl&&frame===frame.page().mainFrame()
      && request.method()==='POST'&&url.origin==='https://jobs.ashbyhq.com'&&page.origin===url.origin
      && url.pathname==='/api/non-user-graphql'&&routing
      && Object.keys(body).length===3&&body.operationName==='ApiSearchSchoolByCanonicalName'
      && typeof body.query==='string'&&body.query.replace(/[\s,]/g,'')===SCHOOL_QUERY.replace(/[\s,]/g,'')
      && Object.keys(values).length===2&&values.organizationHostedJobsPageName===page.pathname.split('/')[1]
      && [search.name,''].includes(values.name);
  }catch{return false;}
}

// Every incremental edit receives a matching server acknowledgement before the next control changes.
async function saveEducationValue(state,field,value,action,check) {
  const edit=armAshbyAnswer(state,field,value);
  if (!edit) throw new Error('The employer education definition is missing.');
  check();await action();await waitForAshbySave(edit,check);
  return JSON.parse(JSON.stringify(value));
}

// Apollo may reuse the first row's cached lookup; retain only its verified exact result in this page generation.
async function findEducationSchool(page,state,input,name,check) {
  const search={name,pageUrl:page.url(),generation:state.generation,expires:Date.now()+30000};state.ashbySchoolSearch=search;
  const cached=state.ashbySchoolMatch;
  const reusable=cached?.name===name&&cached.pageUrl===search.pageUrl&&cached.generation===search.generation;
  let result=reusable?[cached.school]:undefined,error;
  const listener=async response=>{
    const request=response.request();
    if (!educationSchoolRequest(request,state)||request.postDataJSON().variables.name!==name) return;
    try{const body=await response.json();if(!response.ok()||body.errors?.length)throw Error();result=body.data?.result;}
    catch{error=true;}
  };
  page.on('response',listener);
  try {
    check();await input.fill(name,{signal:check.signal});
    const deadline=Date.now()+8000;
    while(!result&&!error&&Date.now()<deadline){check();await page.waitForTimeout(100);}
    check();
    if(error)throw new Error('The employer school lookup failed.');
    if(!Array.isArray(result))throw new Error('The employer school lookup response was not observed.');
    const normalize=value=>String(value||'').normalize('NFKC').trim().replace(/\s+/g,' ').toLowerCase();
    const matches=result.filter(item=>item?.id&&normalize(item.name)===normalize(name));
    if(matches.length!==1)throw new Error('The employer school lookup did not return one exact institution match.');
    const school=JSON.parse(JSON.stringify(matches[0]));
    state.ashbySchoolMatch={name,pageUrl:search.pageUrl,generation:search.generation,school};
    return JSON.parse(JSON.stringify(school));
  }finally{page.removeListener('response',listener);}
}
module.exports={educationRequest:educationSchoolRequest,educationSchoolRequest,findEducationSchool,saveEducationValue,SCHOOL_QUERY};

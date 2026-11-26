// Entry creates an empty candidate-session payload, not a completed application.
function successFactorsEntry(request, state) {
  const permit = state.employerEntry;
  if (!permit || permit.request || Date.now() >= permit.expires) return false;
  try {
    const url = new URL(request.url()), frame = request.frame(), data = request.postDataJSON();
    if (request.method() !== 'POST' || url.href !== 'https://jobs.supermicro.com/services/cas/createpayload/'
        || frame !== frame.page().mainFrame() || frame.url() !== permit.pageUrl) return false;
    const context = data.context, agent = data.agent;
    if (JSON.stringify(Object.keys(data).sort()) !== JSON.stringify(['agent','context','sourceData']) || data.sourceData !== '') return false;
    const expected = {action:'apply',locale:'en_US',source:'',sourceId:'JATS-supermicro',categoryID:'',
      jobID:Number(permit.posting),brand:'',email:'',signedToken:''};
    if (!context || Object.keys(context).length !== Object.keys(expected).length
        || Object.entries(expected).some(([key,value])=>context[key] !== value)) return false;
    const empty = ['keywords','location','q','locationSearch','geoLocation','longitude','latitude','distance','units','country','filterString'];
    if (!agent || Object.keys(agent).length !== empty.length+2 || empty.some(key=>agent[key] !== '')
        || agent.frequency !== 7 || !agent.facets || Object.keys(agent.facets).length) return false;
    permit.request = request;
    return true;
  } catch { return false; }
}

// The employer response, not a model-supplied URL, defines the account handoff.
function handoffFields(body) {
  if (!body || String(body.success).toLowerCase() !== 'true' || body.srCSBEnabled) return null;
  const target = new URL(body.targetUrl);
  if (target.origin !== 'https://career4.successfactors.com' || target.pathname !== '/careers'
      || target.username || target.password) return null;
  const required = ['correlation_Id','company','lang','clientId','socialApply','career_ns','site','career_job_req_id','jobPipeline'];
  const optional = ['externalApply_correlation_Id','isInternalUser','brandUrl','showAlertCreated'];
  const fields = Object.fromEntries(required.map(key=>[key,body[key] == null ? '' : String(body[key])]));
  for (const key of optional) if (body[key] != null && String(body[key])) fields[key] = String(body[key]);
  const requisition = body.career_job_req_id;
  if (typeof requisition !== 'string' && !Number.isSafeInteger(requisition)) return null;
  if (!/^[0-9]+$/.test(fields.career_job_req_id) || fields.company !== 'supermicro' || !fields.correlation_Id) return null;
  return {url:target.href,fields};
}

// An exact server-issued hidden navigation form cannot authorize arbitrary profile writes.
function successFactorsHandoff(request, state) {
  const handoff = state.employerHandoff;
  if (!handoff || handoff.used || Date.now() >= handoff.expires) return false;
  try {
    const frame = request.frame();
    if (request.method() !== 'POST' || request.url() !== handoff.url || frame.url() !== handoff.pageUrl
        || frame !== frame.page().mainFrame() || !request.isNavigationRequest()) return false;
    const values = new URLSearchParams(request.postData() || '');
    if ([...values].length !== Object.keys(handoff.fields).length
        || Object.entries(handoff.fields).some(([key,value])=>values.getAll(key).length !== 1 || values.get(key) !== value)) return false;
    handoff.used = true;
    return true;
  } catch { return false; }
}

// Compare every hidden input with the trusted response before allowing that form to navigate.
async function continueSuccessFactors(page, state, check) {
  const entry = state.employerEntry;
  if (!entry?.request || page.url() !== entry.pageUrl) return false;
  const response = await entry.request.response();
  if (!response?.ok()) return false;
  const handoff = handoffFields(await response.json());
  if (!handoff) return false;
  const handle = await page.evaluateHandle(({url,fields})=>{
    const forms = [...document.forms].filter(form=>form.action === url && form.method.toLowerCase() === 'post');
    return forms.find(form=>{
      const inputs = [...form.elements].filter(input=>input.name);
      return inputs.length === Object.keys(fields).length && inputs.every(input=>input.type === 'hidden'
        && Object.hasOwn(fields,input.name) && input.value === fields[input.name])
        && new Set(inputs.map(input=>input.name)).size === inputs.length;
    }) || null;
  }, handoff);
  const form = handle.asElement();
  if (!form) return false;
  state.employerHandoff = {...handoff,pageUrl:entry.pageUrl,expires:Date.now()+15000,used:false};
  state.successFactorsBinding = {source_url:entry.pageUrl,tenant:handoff.fields.company,
    requisition:handoff.fields.career_job_req_id,company:entry.company,title:entry.title};
  state.previousPageBlocks = [...(state.previousPageBlocks || []),...state.blocked].slice(-40);state.blocked = [];
  try {
    check();await form.evaluate((form,expires)=>{globalThis.__applyDesk.entryForm=form;globalThis.__applyDesk.entryUntil=expires;form.requestSubmit();},state.employerHandoff.expires);
    await page.waitForURL(url=>url.href !== entry.pageUrl,{timeout:15000});check();
    return true;
  } finally { state.employerHandoff = null; }
}
module.exports = {successFactorsEntry, successFactorsHandoff, continueSuccessFactors, handoffFields};

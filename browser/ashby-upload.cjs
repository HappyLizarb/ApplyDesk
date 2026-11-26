const crypto = require('node:crypto');
const path = require('node:path');
const ATTACH_HASH = 'a6798f9c5e490dcde33477f1f5c25a95672376d6959178224dedbc543df21dc2';
const HANDLE_HASH = '512339bdc70b71ab15852e143749296bc8fcb4e36d425f1237dd1554468b7022';

// The ATS may request a signed upload only for the document selected by this fill operation.
function ashbyUploadRequest(request, state) {
  const plan = state.ashbyUploadPlan;
  if (!plan || plan.request || Date.now() >= plan.expires) return false;
  try {
    const url = new URL(request.url()), page = new URL(request.frame().url()), data = request.postDataJSON();
    if (request.method() !== 'POST' || url.origin !== 'https://jobs.ashbyhq.com' || url.pathname !== '/api/non-user-graphql'
        || page.href !== plan.pageUrl || request.frame() !== request.frame().page().mainFrame()) return false;
    if (data.operationName !== 'ApiCreateFileUploadHandle' || Object.keys(data).length !== 3
        || crypto.createHash('sha256').update(data.query).digest('hex') !== HANDLE_HASH) return false;
    const values = data.variables;
    if (!values || Object.keys(values).length !== 5 || values.organizationHostedJobsPageName !== page.pathname.split('/')[1]
        || values.filename !== plan.filename || values.contentLength !== plan.size || values.contentType !== 'application/pdf'
        || typeof values.fileUploadContext !== 'string' || !/^[A-Za-z]{1,80}$/.test(values.fileUploadContext)) return false;
    plan.request = request;
    return true;
  } catch { return false; }
}

// Only the matching official response can authorize a signed storage destination.
function trackAshbyUploads(page, state) {
  state.ashbyFiles = [];
  if (state.ashbyUploadTracking) return;
  state.ashbyUploadTracking = true;
  page.on('response', async response => {
    const permit = state.uploadPermit;
    if (permit?.request === response.request() && response.ok() && permit.ashby) state.ashbyFiles.push({...permit, expires:Date.now()+30000});
    const plan = state.ashbyUploadPlan;
    if (!plan || response.request() !== plan.request) return;
    try {
      const signed = (await response.json()).data?.fileUploadHandle, url = new URL(signed.url);
      if (state.ashbyUploadPlan !== plan || page.url() !== plan.pageUrl || Date.now() >= plan.expires
          || url.protocol !== 'https:' || !/^[a-z0-9.-]+\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$/.test(url.hostname)
          || url.port || url.username || url.password || url.search || url.pathname !== '/' || typeof signed.fields?.key !== 'string') return;
      state.uploadPermit = {...plan, handle:signed.handle, ashby:true, request:null, used:false,
        policy:{url:url.href, pageUrl:plan.pageUrl, fields:signed.fields, key:signed.fields.key}};
    } catch { /* A failed signing response never authorizes an upload. */ }
    finally { plan.resolvePolicy?.(); }
  });
}

// Chrome may start storage upload before the controller finishes reading its signing response.
async function waitForAshbyUploadPolicy(request, state) {
  const plan = state.ashbyUploadPlan;
  if (!plan?.policyReady || !plan.request || state.paused || request.method() !== 'POST') return;
  const url = new URL(request.url());
  let frame;
  try { frame = request.frame(); } catch { return; }
  if (url.protocol !== 'https:' || !/^[a-z0-9.-]+\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$/.test(url.hostname)
      || frame !== frame.page().mainFrame() || frame.url() !== plan.pageUrl) return;
  try {
    const form = await new Response(request.postDataBuffer(), {headers: {'content-type': request.headers()['content-type']}}).formData();
    const file = form.get('file');
    if (!file || file.name !== plan.filename || file.type !== 'application/pdf') return;
  } catch { return; }
  if (state.ashbyUploadPlan !== plan || state.paused) return;
  let timer;
  try {
    await Promise.race([plan.policyReady, new Promise(resolve => {
      timer = setTimeout(resolve, Math.max(0, Math.min(5000, plan.expires - Date.now())));
    })]);
  } finally { clearTimeout(timer); }
}

// Await server acceptance of the exact resume bytes before the normal independent file readback.
async function uploadAshbyResume(page, state, field, element, filePath, sha256, bytes, check) {
  if (path.extname(filePath).toLowerCase() !== '.pdf') throw new Error('This Ashby upload currently supports the unchanged PDF resume.');
  state.ashbyUploadPlan = {pageUrl:page.url(), filename:path.basename(filePath), size:bytes.length,
    sha256, bytes, binding:field.ashby_binding, definition:state.ashbyForms?.get(field.ashby_binding.form)?.definition, expires:Date.now()+30000, request:null};
  const plan = state.ashbyUploadPlan;
  plan.policyReady = new Promise(resolve => { plan.resolvePolicy = resolve; });
  state.uploadPermit = null;
  const response = page.waitForResponse(item=>!!state.uploadPermit?.request && item.request() === state.uploadPermit.request, {timeout:30000});
  response.catch(()=>{});
  const attached = page.waitForResponse(item=>state.ashbyFiles.some(file=>file.attachRequest === item.request()), {timeout:30000});
  attached.catch(()=>{});
  try {
    check();await element.setInputFiles(filePath, {signal:check.signal});
    const result = await response;check();
    if (!result.ok()) throw new Error('Ashby resume storage rejected the upload.');
    const attachment = await attached, body = await attachment.json();check();
    const form = body.data?.setFormValueToFile, entry = form?.sections?.flatMap(section=>section.fieldEntries || []).find(item=>item.field.path === field.ashby_binding.path);
    if (!attachment.ok() || body.errors?.length || entry?.fieldValue?.filename !== path.basename(filePath)) throw new Error('Ashby did not attach the uploaded resume to the form.');
    const saved = state.ashbyFiles.find(file=>file.attachRequest === attachment.request());
    if (saved) saved.verified = true;
  } finally { plan.resolvePolicy();state.ashbyUploadPlan = null;state.uploadPermit = null; }
}
// Attach only the handle returned for this successfully uploaded and hashed document.
function ashbyAttachRequest(request, state) {
  try {
    const page = new URL(request.frame().url()), url = new URL(request.url()), data = request.postDataJSON();
    if (request.method() !== 'POST' || url.origin !== 'https://jobs.ashbyhq.com' || url.pathname !== '/api/non-user-graphql'
        || request.frame() !== request.frame().page().mainFrame() || data.operationName !== 'ApiSetFormValueToFile'
        || Object.keys(data).length !== 3 || crypto.createHash('sha256').update(data.query).digest('hex') !== ATTACH_HASH) return false;
    const values = data.variables;
    if (!values || Object.keys(values).length !== 5 || values.organizationHostedJobsPageName !== page.pathname.split('/')[1]) return false;
    const file = (state.ashbyFiles || []).find(item=>!item.attachRequest && item.pageUrl === page.href && Date.now()<item.expires
      && typeof item.handle === 'string' && values.fileHandle === item.handle && values.path === item.binding.path
      && values.formRenderIdentifier === item.binding.form && values.formDefinitionIdentifier === item.definition);
    if (!file) return false;
    file.attachRequest = request;
    return true;
  } catch { return false; }
}
module.exports = {ashbyAttachRequest, ashbyUploadRequest, trackAshbyUploads, uploadAshbyResume, waitForAshbyUploadPolicy};

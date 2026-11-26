const crypto = require('node:crypto');
const path = require('node:path');

// Read signing instructions only from the ATS's own response in the active application document.
function trackUploads(page, state) {
  state.uploadPolicies = {};state.completedUploads = [];state.uploadPermit = null;
  if (state.uploadTracking) return;
  state.uploadTracking = true;
  page.on('response', async response => {
    try {
      const source = new URL(response.url()), current = new URL(page.url());
      if (response.frame() !== page.mainFrame() || response.request().method() !== 'GET'
        || source.origin !== 'https://boards.greenhouse.io' || source.pathname !== '/uncacheable_attributes/presigned_fields'
        || current.hostname !== 'job-boards.greenhouse.io' || current.pathname !== '/embed/job_app') return;
      const policy = await response.json(), target = new URL(policy.url);
      if (target.protocol !== 'https:' || !/^[a-z0-9.-]+\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com$/.test(target.hostname)
        || target.port || target.username || target.password || target.search || target.pathname !== '/') return;
      if (page.url() !== current.href || !policy.resume?.fields || typeof policy.resume.key !== 'string') return;
      state.uploadPolicies.resume = {...policy.resume, url: target.href, pageUrl: current.href};
    } catch { /* Unreadable signing responses never grant upload permission. */ }
  });
}

// A one-use upload permit binds the observed signed request to the exact approved document bytes.
async function authorizedUpload(request, state) {
  const permit = state.uploadPermit;
  if (!permit?.policy || permit.used || Date.now() >= permit.expires || request.method() !== 'POST' || request.url() !== permit.policy.url) return false;
  try {
    if (request.frame().url() !== permit.policy.pageUrl) return false;
    const form = await new Response(request.postDataBuffer(), {headers: {'content-type': request.headers()['content-type']}}).formData();
    const signed = permit.policy.fields, allowed = new Set([...Object.keys(signed), 'utf8', 'key', 'authenticity_token', 'Content-Type', 'file']);
    if ([...form.keys()].some(key => !allowed.has(key) || form.getAll(key).length !== 1)) return false;
    if (Object.entries(signed).some(([key, value]) => typeof value !== 'string' || form.get(key) !== value)) return false;
    const file = form.get('file'), key = form.get('key');
    const pattern = permit.policy.key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace('\\{timestamp\\}', '\\d+').replace('\\{unique_id\\}', '[a-z0-9]+');
    if (typeof key !== 'string' || !new RegExp('^' + pattern + '$').test(key) || key.includes('..')) return false;
    if (!file || file.name !== permit.filename || typeof file.arrayBuffer !== 'function') return false;
    if (crypto.createHash('sha256').update(permit.bytes).digest('hex') !== permit.sha256) return false;
    // Chrome omits binary file bodies from interception: supply the independently verified bytes.
    form.set('file', new Blob([permit.bytes], {type: file.type}), permit.filename);
    const encoded = new Request(request.url(), {method: 'POST', body: form});
    const postData = Buffer.from(await encoded.arrayBuffer());
    if (state.paused || state.uploadPermit !== permit) return false;
    const headers = {...request.headers(), 'content-type': encoded.headers.get('content-type')};
    delete headers['content-length'];
    Object.assign(permit, {used: true, request, remoteUrl: new URL(key, permit.policy.url).href});
    return {postData, headers};
  } catch { return false; }
}

// A successful HTTP upload and the site's visible attached filename are both required.
async function uploadResume(page, state, field, element, filePath, sha256, bytes, check) {
  const filename = path.basename(filePath), size = bytes.length;
  const previous = (state.completedUploads || []).find(item => item.field.field_id === field.field_id);
  if (previous) {
    if (previous.sha256 !== sha256 || previous.filename !== filename) throw new Error('Remove the previous resume before changing its document version.');
    return {field_id: field.field_id, matches: true, files: [{name: filename, size}], sha256};
  }
  const policy = state.uploadPolicies?.resume;
  if (!policy || policy.pageUrl !== page.url() || await element.getAttribute('id') !== 'resume') throw new Error('The resume upload has no verified signing response.');
  const permit = {policy, filename, sha256, size, bytes, expires: Date.now() + 30000, used: false};
  state.uploadPermit = permit;
  const response = page.waitForResponse(response => response.request() === permit.request, {timeout: 30000});
  response.catch(() => {});
  try {
    check();await element.setInputFiles(filePath, {signal: check.signal});
    const uploaded = await response;check();
    if (!uploaded.ok()) throw new Error('The resume storage service did not accept the upload.');
    await page.waitForFunction(({id, filename}) => {
      const group = globalThis.__applyDesk.elements.get(id);
      return group?.isConnected && group.querySelector('.file-upload__filename')?.textContent.trim() === filename;
    }, {id: field.group_id, filename}, {timeout: 10000});
    check();state.completedUploads.push({field, filename, sha256, size, remoteUrl: permit.remoteUrl});
    return {field_id: field.field_id, matches: true, files: [{name: filename, size}], sha256};
  } finally { state.uploadPermit = null; }
}

// React removes the input after upload; retain its actual File only while the attachment is visible.
async function appendUploadedFiles(page, state, snapshot) {
  for (const item of state.completedUploads || []) {
    if (snapshot.fields.some(field => field.field_id === item.field.field_id)) continue;
    const visible = await page.evaluate(({id, filename}) => {
      const group = globalThis.__applyDesk.elements.get(id);
      return group?.isConnected && group.getClientRects().length > 0
        && group.querySelector('.file-upload__filename')?.textContent.trim() === filename
        && !group.querySelector('[role="progressbar"]');
    }, {id: item.field.group_id, filename: item.filename});
    if (visible) snapshot.fields.push({...item.field, remote_uploaded: true, files: [{name: item.filename, size: item.size}]});
  }
}

// The final application must reference the exact file the audited upload accepted.
function uploadedResumeMatches(request, state) {
  const uploaded = state.completedUploads || [];
  if (!uploaded.length) return false;
  try {
    const data = request.postDataJSON().job_application;
    return uploaded.length === 1 && data.resume_url === uploaded[0].remoteUrl && data.resume_url_filename === uploaded[0].filename;
  } catch { return false; }
}

module.exports = {trackUploads, authorizedUpload, uploadResume, appendUploadedFiles, uploadedResumeMatches};

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {agentElement} = require('./agent-page.cjs');
const {agentFileRecords} = require('./agent-page-files.cjs');

// The backend chooses an approved document; model-provided filesystem paths never reach this helper.
async function agentUpload(page, state, field, upload, check) {
  if (field.type !== 'file' || field.disabled || (!field.visible && !field.upload_control_id))
    throw new Error('Choose an observed enabled upload control.');
  if (!upload || typeof upload.path !== 'string' || !/^[a-f0-9]{64}$/.test(upload.sha256 || ''))
    throw new Error('An approved document and its exact SHA-256 are required.');
  const resolved = await fs.realpath(upload.path);
  const roots = await Promise.all((state.uploadRoots || []).map(root => fs.realpath(root)));
  if (!roots.some(root => resolved.startsWith(root + path.sep))) throw new Error('Upload is outside the approved document folder.');
  const bytes = await fs.readFile(resolved);
  if (bytes.length > 20 * 1024 * 1024) throw new Error('The selected attachment exceeds the upload verification limit.');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== upload.sha256) throw new Error('The selected document changed.');
  const element = await agentElement(page, field.id, true);
  check(); await element.setInputFiles(resolved, {timeout: 10000, signal: check.signal});
  check();
  const files = await agentFileRecords(page, field.id), actual = files[0];
  if (files.length !== 1 || actual.name !== path.basename(resolved) || actual.size !== bytes.length || actual.sha256 !== digest)
    throw new Error('The selected browser attachment could not be verified. Observe its upload result before retrying.');
  check();
  return {element_id: field.id, document_id: upload.document_id, kind: upload.kind, sha256: digest,
    filename: actual.name, size: actual.size, files, selected_file_verified: true, upload_confirmed: false};
}

module.exports = {agentUpload};

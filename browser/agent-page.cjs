const crypto = require('node:crypto');
const {checkpoint} = require('./control.cjs');
const {agentFileRecords} = require('./agent-page-files.cjs');
const {collectAgentPage, describeAgentElement, agentUploadControl, agentLabelText, agentPageStatus} = require('./agent-page-dom.cjs');

// Generic interaction belongs only to an explicitly owned application tab using normal Chrome networking.
function agentPageCheck(state) {
  if (!state.normalNetworking || !state.workspace || !state.guardInstalled || state.manual)
    throw new Error('Agent browsing requires an active owned application page.');
  const check = checkpoint(state);
  check();
  return check;
}

// Observation never clicks, expands controls, navigates, or changes field values.
async function agentObserve(page, state) {
  const check = agentPageCheck(state);
  const script = `(() => {const agentPageStatus=${agentPageStatus};const agentLabelText=${agentLabelText};const agentUploadControl=${agentUploadControl};const describeAgentElement=${describeAgentElement};return (${collectAgentPage})();})()`;
  const snapshot = redactAgentValues(await page.evaluate(script), state.agentRedactions || []);
  for (const element of snapshot.elements.filter(item => item.type === 'file' && item.files.length))
    element.files = await agentFileRecords(page, element.id);
  check();
  snapshot.generation = state.generation || 0;
  snapshot.snapshot_id = crypto.createHash('sha256').update(JSON.stringify(snapshot)).digest('hex');
  if (state.agentObservedUrl !== snapshot.url) {
    state.agentPreviousUrl = state.agentObservedUrl || null;
    state.agentObservedUrl = snapshot.url;
  }
  return snapshot;
}

// A password reflected by a page after its form disappears must stay out of later model observations.
function redactAgentValues(value, secrets) {
  if (typeof value === 'string') return secrets.reduce((text, secret) => text.split(secret).join('[redacted]'), value);
  if (Array.isArray(value)) return value.map(item => redactAgentValues(item, secrets));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactAgentValues(item, secrets)]));
  return value;
}

// Every action resolves an element retained by the current document, never a model-written selector.
async function agentElement(page, id, allowHiddenFile = false) {
  if (typeof id !== 'string' || !id) throw new Error('An observed element ID is required.');
  const handle = await page.evaluateHandle(id => globalThis.__applyDesk?.elements.get(id), id);
  const element = handle.asElement();
  if (!element || !await element.evaluate(item => item.isConnected)
      || (!await element.isVisible() && !(allowHiddenFile && await element.evaluate(item => item.type === 'file')))) {
    const error = new Error('The observed element changed. Observe the page again.');
    error.code = 'AGENT_STALE_ELEMENT';throw error;
  }
  return element;
}

module.exports = {agentObserve, agentPageCheck, agentElement};

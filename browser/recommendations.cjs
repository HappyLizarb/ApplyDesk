const {assertPublicUrl} = require('./guard.cjs');
const {jobLink, sourceOrigin} = require('./recommendations-url.cjs');
const {readJob, waitForJob, expandDescription} = require('./recommendations-dom.cjs');

// Reports contain job-only data and aggregate guard state, never sidebar or account text.
function report(session, status, issues = [], jobs = [], warnings = []) {
  return {source: session.state.source, status, message: issues[0] || '', issues, jobs, warnings: [...new Set(warnings)],
    guard: {manual: session.state.manual, mutations_blocked: false, page_networking: 'normal',
      form_submission_blocked: !session.state.manual, final_paths_blocked: !session.state.manual,
      blocked_count: session.state.blocked.length}};
}

// Each navigation starts from an observed source URL; only visible job content is copied.
async function visit(session, candidate) {
  const source = session.state.source, origin = sourceOrigin(session);
  const link = jobLink(candidate.url, source, origin);
  if (!link || link.id !== String(candidate.id)) throw new Error('The source URL does not match its observed job identity.');
  await assertPublicUrl(link.url, session.state.allowLocal);
  await session.page.goto(link.url, {waitUntil: 'domcontentloaded', timeout: 10000});
  return captureJob(session, link);
}

// Capture only the selected job after validating the live destination and its scoped description.
async function captureJob(session, link) {
  const source = session.state.source, origin = sourceOrigin(session);
  await assertPublicUrl(session.page.url(), session.state.allowLocal);
  await waitForJob(session.page, source, link.id, origin);
  await expandDescription(session.page, source, link.id, origin);
  const job = await readJob(session.page, source, link.id, origin);
  job.url = job.url || link.url;
  const warnings = session.state.blocked.length ? ['Private-network or possible final-submission requests were blocked in this source session.'] : [];
  if (job.employer_url) {
    try { await assertPublicUrl(job.employer_url, session.state.allowLocal); }
    catch { job.employer_url = ''; job.issues.push('The employer destination could not be validated as public.'); }
  }
  job.complete = job.complete && job.issues.length === 0;
  return {job, warnings};
}

// Fresh preparation reads one stored source identity without entering manual browser mode.
async function sourceJob(session, params) {
  if (session.state.manual) throw new Error('Close the manual source session before a fresh guarded job read.');
  const {job, warnings} = await visit(session, params);
  return {...report(session, job.complete ? 'ready' : 'blocked', job.issues, [], warnings), job};
}

// Source sessions expose no application, form filling, generic click, or script commands.
async function sourceCommand(session, method, params) {
  if (params.source && params.source !== session.state.source) throw new Error('The command source does not match this browser session.');
  if (method === 'source_job') return sourceJob(session, params);
  throw new Error('Only recommendation reading commands are allowed in this browser session.');
}

module.exports = {sourceCommand};

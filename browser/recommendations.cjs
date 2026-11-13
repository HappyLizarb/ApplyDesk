const {assertPublicUrl, armSource} = require('./guard.cjs');
const {START_URLS, jobLink, sourceOrigin} = require('./recommendations-url.cjs');
const {readFeed, readJob, waitForJob, waitForFeed, followFeedLink, followLinkedInCard, expandDescription} = require('./recommendations-dom.cjs');
const {searchJobs, hasSearchInput} = require('./source-search.cjs');

// Reports contain job-only data and aggregate guard state, never sidebar or account text.
function report(session, status, issues = [], jobs = [], warnings = []) {
  return {source: session.state.source, status, message: issues[0] || '', issues, jobs, warnings: [...new Set(warnings)],
    guard: {manual: session.state.manual, mutations_blocked: false, page_networking: 'normal',
      form_submission_blocked: !session.state.manual, final_paths_blocked: !session.state.manual,
      blocked_count: session.state.blocked.length}};
}

// Sign-in is user-controlled in a dedicated profile; no credential fields are read or filled.
async function login(session, restart) {
  await restart(session, true);
  session.state.sourceSearch = false;
  const url = new URL(START_URLS[session.state.source]);
  const destination = new URL(url.pathname, sourceOrigin(session)).href;
  await assertPublicUrl(destination, session.state.allowLocal);
  await session.page.goto(destination, {waitUntil: 'domcontentloaded', timeout: 20000});
  await session.page.bringToFront();
  return status(session);
}

// Login may return in another source tab, but unrelated tabs and their contents are ignored.
async function status(session) {
  for (const page of session.context.pages().filter(page => !page.isClosed()).slice(0, 5)) {
    if (!page.url().startsWith(sourceOrigin(session) + '/')) continue;
    let feed;
    try { feed = await readFeed(page, session.state.source, sourceOrigin(session), session.state.sourceSearch); }
    catch { continue; }
    if (feed.challenge) return report(session, session.state.manual ? 'waiting_signin' : 'blocked', ['The source requires a security check. Complete it manually.']);
    if (!feed.signedOut && await hasSearchInput(page, session.state.source)) {
      session.page = page;
      return {...report(session, 'ready'), observed_count: feed.links.length};
    }
    if (feed.loading) return report(session, 'waiting_signin', ['The recommendation headings are visible, but no job cards have loaded yet. Keep the feed open.']);
    if (feed.ready) {
      session.page = page;
      return {...report(session, 'ready'), observed_count: feed.links.length};
    }
  }
  return report(session, 'waiting_signin', ['Sign in and open your recommended jobs in the dedicated browser.']);
}

// Each navigation starts from an observed source URL; only visible job content is copied.
async function visit(session, candidate, followLink = false) {
  const source = session.state.source, origin = sourceOrigin(session);
  const link = jobLink(candidate.url, source, origin);
  if (!link || link.id !== String(candidate.id)) throw new Error('The source URL does not match its observed job identity.');
  await assertPublicUrl(link.url, session.state.allowLocal);
  if (followLink) await followFeedLink(session.page, link, source, origin, session.state.sourceSearch);
  else await session.page.goto(link.url, {waitUntil: 'domcontentloaded', timeout: 10000});
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
  return {job, warnings, challenge: (await readFeed(session.page, source, origin)).challenge};
}

// Return only through the history entry created by our own observed recommendation click.
async function returnToFeed(session, navigation, before) {
  const after = await navigation.send('Page.getNavigationHistory');
  const entry = before.entries[before.currentIndex];
  if (!entry || new URL(entry.url).origin !== sourceOrigin(session)) throw new Error('The observed feed belongs to a different website.');
  if (after.currentIndex > before.currentIndex && after.entries[before.currentIndex]?.id === entry.id) {
    await navigation.send('Page.navigateToHistoryEntry', {entryId: entry.id});
    await session.page.waitForURL(entry.url, {timeout: 10000});
  } else if (session.page.url() !== entry.url) {
    await assertPublicUrl(entry.url, session.state.allowLocal);
    await session.page.goto(entry.url, {waitUntil: 'domcontentloaded', timeout: 10000});
  }
  await waitForFeed(session.page, session.state.source, sourceOrigin(session), session.state.sourceSearch);
}

// Both sources retain the signed-in browser and stop this bounded scan at a security challenge.
async function scanObserved(session, candidates) {
  await armSource(session.context, session.state);
  const navigation = await session.context.newCDPSession(session.page), jobs = [], issues = [], warnings = [];
  const deadline = Date.now() + 140000;
  try {
    for (const candidate of candidates) {
      if (Date.now() > deadline) { issues.push('The bounded source scan ended before all listings were inspected.'); break; }
      const before = await navigation.send('Page.getNavigationHistory');
      let observed = candidate;
      try {
        if (candidate.card) observed = await followLinkedInCard(session.page, candidate, sourceOrigin(session));
        const result = candidate.card ? await captureJob(session, observed) : await visit(session, candidate, session.state.source === 'handshake');
        jobs.push(result.job); warnings.push(...result.warnings);
        if (result.challenge) { issues.push('The source requires a manual security check; the scan stopped.'); break; }
      } catch {
        const challenge = (await readFeed(session.page, session.state.source, sourceOrigin(session)).catch(() => ({}))).challenge;
        const issue = challenge ? 'The source requires a manual security check; the scan stopped.' : 'This listing could not be read completely; it was skipped.';
        if (observed.id && !jobs.some(job => job.id === observed.id)) jobs.push(incompleteJob(observed, issue));
        issues.push(issue); if (challenge) break;
      }
      if (session.state.source === 'handshake' && candidate !== candidates.at(-1)) {
        try { await returnToFeed(session, navigation, before); }
        catch { issues.push('The recommendations page could not be restored; captured jobs were retained.');break; }
      }
    }
  } finally { await navigation.detach().catch(() => {}); }
  warnings.push('Only the currently loaded job cards were inspected. Change the query to search another result set.');
  return report(session, jobs.some(job => job.complete) ? 'ready' : 'blocked', issues, jobs, warnings);
}

// Capture the loaded recommendation anchors before enabling automation's form guard.
async function scan(session, params) {
  const limit = params.limit ?? 5;
  if (!Number.isInteger(limit) || limit < 1 || limit > 10) throw new Error('Choose a source import limit from 1 to 10.');
  const query = params.query ?? '';
  if (typeof query !== 'string' || query.length > 300) throw new Error('Use a search query of at most 300 characters.');
  const current = await status(session);
  if (current.status !== 'ready') return current;
  const feed = query.trim() ? await searchJobs(session, query) : await readFeed(session.page, session.state.source, sourceOrigin(session));
  const candidates = (session.state.source === 'linkedin' && feed.cards?.length ? feed.cards : feed.links).slice(0, limit);
  if (query.trim() && feed.empty && !candidates.length) return report(session, 'ready');
  if (!candidates.length) return report(session, 'blocked', ['No job links were loaded in the selected source results.']);
  return scanObserved(session, candidates);
}

// An observed link remains useful when its full description cannot be read safely.
function incompleteJob(candidate, issue) {
  return {id: candidate.id, url: candidate.url, title: '',
    company: '', location: '', text: '', employer_url: '', complete: false, issues: [issue]};
}

// Fresh preparation reads one stored source identity without entering manual browser mode.
async function sourceJob(session, params) {
  if (session.state.manual) throw new Error('Close the manual source session before a fresh guarded job read.');
  const {job, warnings} = await visit(session, params);
  return {...report(session, job.complete ? 'ready' : 'blocked', job.issues, [], warnings), job};
}

// Source sessions expose no application, form filling, generic click, or script commands.
async function sourceCommand(session, method, params, restart) {
  if (params.source && params.source !== session.state.source) throw new Error('The command source does not match this browser session.');
  if (method === 'source_login') return login(session, restart);
  if (method === 'source_status') return status(session);
  if (method === 'source_scan') return scan(session, params);
  if (method === 'source_job') return sourceJob(session, params);
  throw new Error('Only recommendation reading commands are allowed in this browser session.');
}

module.exports = {sourceCommand};

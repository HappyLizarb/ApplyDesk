const {assertPublicUrl} = require('./guard.cjs');

// Follow only an observed Greenhouse embed carrying the parent page's exact job ID.
async function openApplicationFrame(session, check) {
  const page = session.page, parent = new URL(page.url());
  const jobId = parent.searchParams.get('gh_jid');
  if (!/^\d+$/.test(jobId || '')) return null;
  const frames = await page.locator('iframe').evaluateAll(elements => elements
    .filter(element => element.getClientRects().length).map(element => element.src));
  const candidates = frames.filter(raw => {
    try {
      const url = new URL(raw);
      return url.protocol === 'https:' && ['job-boards.greenhouse.io', 'boards.greenhouse.io'].includes(url.hostname)
        && url.pathname === '/embed/job_app' && url.searchParams.get('token') === jobId
        && !!url.searchParams.get('for') && !url.username && !url.password;
    } catch { return false; }
  });
  if (!candidates.length) return null;
  if (candidates.length !== 1) throw new Error('Multiple embedded application forms need review.');
  await assertPublicUrl(candidates[0], session.state.allowLocal);
  check();
  await page.goto(candidates[0], {waitUntil: 'domcontentloaded', timeout: 30000, signal: check.signal});
  check();
  return {parent_url: parent.href, frame_url: page.url(), job_id: jobId};
}

module.exports = {openApplicationFrame};

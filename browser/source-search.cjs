const {assertPublicUrl} = require('./guard.cjs');
const {sourceOrigin} = require('./recommendations-url.cjs');
const {readFeed} = require('./recommendations-dom.cjs');

// Locate a visible job-keyword field, excluding location, authentication and unrelated site search.
function searchInput(source) {
  const onSearch = source === 'handshake' ? /^\/job-search(?:\/|$)/.test(location.pathname) : /^\/jobs(?:\/|$)/.test(location.pathname);
  const candidates = [...document.querySelectorAll('input')].filter(input => {
    if (!input.getClientRects().length || input.disabled || input.readOnly || !['text', 'search'].includes(input.type)) return false;
    const label = [input.getAttribute('aria-label'), input.placeholder, input.name,
      ...[...(input.labels || [])].map(item => item.innerText)].filter(Boolean).join(' ');
    if (/location|city|postal|zip code|email|password|username/i.test(label)) return false;
    return /job|keyword|title|skill|company/i.test(label) ||
      onSearch && (input.type === 'search' || /search/i.test(label)) && !!input.closest('main,[role="main"]');
  });
  return candidates.length === 1 ? candidates[0] : null;
}

// A loaded keyword control can establish search readiness even when recommendations are empty.
async function hasSearchInput(page, source) {
  const handle = await page.evaluateHandle(searchInput, source);
  try { return !!handle.asElement(); }
  finally { await handle.dispose(); }
}

// Navigation uses a Jobs link observed in the page, not a guessed platform endpoint.
function searchNavigation(source) {
  return [...document.querySelectorAll('a[href]')].find(anchor => {
    if (!anchor.getClientRects().length || !/^(jobs|search jobs|find jobs)$/i.test(anchor.innerText.trim())) return false;
    const url = new URL(anchor.href);
    const path = source === 'handshake' ? /^\/job-search\/?$/ : /^\/jobs\/(?:search\/?|search-results\/?)?$/;
    return url.origin === location.origin && path.test(url.pathname) && !anchor.closest('form,button');
  })?.href || '';
}

// Wait briefly for the platform's real search control to hydrate; absence ends the search clearly.
async function inputHandle(session) {
  const deadline = Date.now() + 6000;
  do {
    const handle = await session.page.evaluateHandle(searchInput, session.state.source);
    if (handle.asElement()) return handle;
    await handle.dispose();
    await session.page.waitForTimeout(150);
  } while (Date.now() < deadline);
  return null;
}

// Navigate to the observed Jobs URL so hydration cannot detach the navigation target.
async function openSearch(session) {
  let handle = await session.page.evaluateHandle(searchInput, session.state.source);
  if (handle.asElement()) return handle;
  await handle.dispose();
  const destination = await session.page.evaluate(searchNavigation, session.state.source);
  if (!destination) throw new Error('The platform job-search field is unavailable. Open its Jobs search page and try again.');
  await assertPublicUrl(destination, session.state.allowLocal);
  await session.page.goto(destination, {waitUntil: 'domcontentloaded', timeout: 20000});
  handle = await inputHandle(session);
  if (!handle) throw new Error('The platform job-search field could not be identified. No recommendation scan was substituted.');
  return handle;
}

// Old preference URLs and tracking changes cannot stand in for the submitted keyword search.
async function waitForSearch(session, query, before) {
  const deadline = Date.now() + 12000, origin = sourceOrigin(session);
  let previous = '', stable = 0;
  do {
    if (new URL(session.page.url()).origin !== origin) throw new Error('The job search left the connected platform.');
    const feed = await readFeed(session.page, session.state.source, origin, true);
    if (feed.challenge) throw new Error('The platform requires a manual security check; the search stopped.');
    const url = new URL(session.page.url());
    const terms = ['keywords', 'query', 'q', 'search'].filter(key => url.searchParams.has(key)).map(key => url.searchParams.get(key));
    const reflected = terms.some(value => value.trim().toLowerCase() === query.toLowerCase());
    const ids = JSON.stringify([feed.links.map(link => link.id), (feed.cards || []).map(card => card.jobId || card.label)]);
    const changed = ids !== before.ids || (feed.empty && !before.empty);
    stable = ids === previous ? stable + 1 : 0;
    previous = ids;
    if (feed.ready && stable >= 2 && (terms.length ? reflected && (changed || before.query === query.toLowerCase()) : changed)) return feed;
    await session.page.waitForTimeout(200);
  } while (Date.now() < deadline);
  throw new Error('The search did not produce readable results. The previous page was not imported.');
}

// Type once into the site's own job-search UI; no platform API or hidden request is constructed.
async function searchJobs(session, query) {
  if (typeof query !== 'string' || !query.trim() || query.length > 300) throw new Error('Use a job-search query from 1 to 300 characters.');
  const handle = await openSearch(session), origin = sourceOrigin(session);
  const feed = await readFeed(session.page, session.state.source, origin, true);
  const url = new URL(session.page.url()), queryKey = ['keywords', 'query', 'q', 'search'].find(key => url.searchParams.has(key));
  const before = {ids: JSON.stringify([feed.links.map(link => link.id), (feed.cards || []).map(card => card.jobId || card.label)]),
    query: queryKey ? url.searchParams.get(queryKey).trim().toLowerCase() : '', empty: feed.empty};
  try {
    await handle.asElement().fill(query.trim(), {timeout: 5000});
    await handle.asElement().press('Enter', {timeout: 5000});
  } finally { await handle.dispose(); }
  const results = await waitForSearch(session, query.trim(), before);
  session.state.sourceSearch = true;
  return results;
}

module.exports = {searchJobs, hasSearchInput};

const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');
const {searchJobs} = require('../browser/source-search.cjs');
const {feed, handshake, linkedin} = require('./recommendations-fixtures.cjs');

// Synthetic sites expose only ordinary visible search forms and job links.
function searchPage(source, query = '') {
  const route = source === 'handshake' ? '/job-search' : '/jobs/search/';
  const link = source === 'handshake' ? '/job-search/777' : '/jobs/view/777/';
  const result = query === 'none' ? '<p>No jobs found</p>' : query ? `<h2>Search results</h2><a href="${link}">Hardware Engineer</a>` : '';
  return `<title>Jobs</title><main><form action="${route}" method="get"><label>Job title, keywords, or company
    <input type="search" name="query" value="${query}"></label><button>Search</button></form>${result}</main>`;
}

// Count normal browser navigations so repeated search submissions and accidental writes are visible.
async function fixture() {
  const searches = [], writes = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.test');
    response.setHeader('Content-Type', 'text/html');
    if (request.method !== 'GET') writes.push(url.pathname);
    if (url.pathname === '/home') return response.end(feed('handshake') + '<a href="/job-search">Jobs</a>');
    if (url.pathname === '/jobs/') return response.end(feed('linkedin') + searchPage('linkedin'));
    if (url.pathname === '/job-search' || url.pathname === '/jobs/search/') {
      const query = url.searchParams.get('query') || '';
      if (query) searches.push(query);
      if (query === 'challenge') return response.end('<title>Security verification</title>');
      if (query === 'stale') { response.writeHead(302, {Location: '/jobs/search/?query=previous'}); return response.end(); }
      if (query === 'hydrating') return response.end(feed('linkedin') + searchPage('linkedin') + `<script>
        setTimeout(()=>{document.querySelector('main').innerHTML='<a href="/jobs/view/777/">Hardware Engineer</a>'},800)</script>`);
      if (query === 'hydrating-empty') return response.end(searchPage('linkedin', 'none') + `<script>
        setTimeout(()=>{document.querySelector('main').innerHTML='<a href="/jobs/view/777/">Hardware Engineer</a>'},800)</script>`);
      return response.end(searchPage(url.pathname === '/job-search' ? 'handshake' : 'linkedin', query));
    }
    if (url.pathname === '/job-search/777') return response.end(handshake('777', false).replaceAll('Engineering Intern', 'Hardware Engineer'));
    if (url.pathname === '/jobs/view/777/') return response.end(linkedin(false, '777').replaceAll('Engineering Intern', 'Hardware Engineer'));
    response.end('<title>Fixture</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, searches, writes, origin: `http://127.0.0.1:${server.address().port}`};
}

// Both platforms must use submitted keywords and import search cards instead of old recommendations.
async function testQuery(directory, site, source) {
  const session = await start({dataRoot: path.join(directory, source), source, sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(site.origin + (source === 'handshake' ? '/home' : '/jobs/'));
    const result = await dispatch(session, 'source_scan', {limit: 2, query: 'hardware new grad'});
    assert.equal(result.status, 'ready', JSON.stringify(result));
    assert.equal(result.jobs.length, 1);
    assert.equal(result.jobs[0].id, '777');
    assert.equal(result.jobs[0].title, 'Hardware Engineer');
    assert.equal(result.jobs[0].company, 'Acme Systems');
    assert.equal(result.jobs[0].complete, true);
    assert(site.searches.includes('hardware new grad'));
    assert(!result.jobs.some(job => job.id === '11466492' || job.id === '4474551500'));
    assert(!site.writes.includes('/submit'));
  } finally { await session.context.close(); }
}

// Hydration replaces the Jobs link while its destination is observed, before navigation can begin.
async function testDetachedNavigation(directory, site) {
  const session = await start({dataRoot: path.join(directory, 'detached'), source: 'linkedin', sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(site.origin + '/jobs/');
    await session.page.setContent(feed('linkedin') + '<a id="jobs-navigation" href="/jobs/search/">Jobs</a>');
    await session.page.evaluate(() => {
      const link = document.getElementById('jobs-navigation'), destination = link.href;
      Object.defineProperty(link, 'href', {get() {
        link.replaceWith(link.cloneNode(true));
        sessionStorage.setItem('navigation-replaced', 'true');
        return destination;
      }});
    });
    const result = await dispatch(session, 'source_scan', {limit: 2, query: 'hardware detached'});
    assert.equal(await session.page.evaluate(() => sessionStorage.getItem('navigation-replaced')), 'true');
    assert.equal(result.status, 'ready', JSON.stringify(result));
    assert.equal(result.jobs.length, 1);
    assert.equal(result.jobs[0].id, '777');
    assert.equal(result.jobs[0].complete, true);
    assert.equal(site.searches.filter(query => query === 'hardware detached').length, 1);
    assert(!site.writes.includes('/submit'));
  } finally { await session.context.close(); }
}

// No-results and a manual challenge end once without falling back to unrelated recommendations.
async function testEmptyAndChallenge(directory, site) {
  for (const query of ['none', 'challenge']) {
    const session = await start({dataRoot: path.join(directory, query), source: 'handshake', sourceManual: true,
      sourceTestOrigin: site.origin, headless: true, allowLocal: true});
    try {
      await session.page.goto(site.origin + '/home');
      if (query === 'none') {
        const result = await dispatch(session, 'source_scan', {limit: 2, query});
        assert.equal(result.status, 'ready');
        assert.deepEqual(result.jobs, []);
      } else await assert.rejects(dispatch(session, 'source_scan', {limit: 2, query}), /manual security check/);
      assert.equal(site.searches.filter(value => value === query).length, 1);
    } finally { await session.context.close(); }
  }
}

// An unsupported search surface is a clear error, never a recommendation scan disguised as a search.
async function testMissingSearch(directory, site) {
  const session = await start({dataRoot: path.join(directory, 'missing'), source: 'handshake', sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(site.origin);
    await session.page.setContent(feed('handshake'));
    await assert.rejects(dispatch(session, 'source_scan', {limit: 2, query: 'hardware'}), /job-search field is unavailable/);
    assert(!site.searches.includes('hardware'));
  } finally { await session.context.close(); }
}

// An unrelated preference query may change both URL and cards without answering the typed request.
async function testStaleQuery(directory, site) {
  const session = await start({dataRoot: path.join(directory, 'stale'), source: 'linkedin', sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(site.origin + '/jobs/');
    await assert.rejects(searchJobs(session, 'stale'), /previous page was not imported/i);
    assert.equal(site.searches.filter(query => query === 'stale').length, 1);
  } finally { await session.context.close(); }
}

// URL updates precede asynchronous result rendering; repeated identical queries may retain their IDs.
async function testDelayedResults(directory, site) {
  const session = await start({dataRoot: path.join(directory, 'hydrating'), source: 'linkedin', sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await session.page.goto(site.origin + '/jobs/');
    const fresh = await searchJobs(session, 'hydrating');
    assert.deepEqual(fresh.links.map(link => link.id), ['777']);
    await session.page.goto(site.origin + '/jobs/search/?query=none');
    const fromEmpty = await searchJobs(session, 'hydrating-empty');
    assert.deepEqual(fromEmpty.links.map(link => link.id), ['777']);
    await session.page.goto(site.origin + '/jobs/search/?query=hardware');
    const repeated = await searchJobs(session, 'hardware');
    assert.deepEqual(repeated.links.map(link => link.id), ['777']);
  } finally { await session.context.close(); }
}

(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-query-test-')), site = await fixture();
  try {
    await testQuery(directory, site, 'handshake');
    await testQuery(directory, site, 'linkedin');
    await testDetachedNavigation(directory, site);
    await testEmptyAndChallenge(directory, site);
    await testMissingSearch(directory, site);
    await testStaleQuery(directory, site);
    await testDelayedResults(directory, site);
    console.log('Connected search passed: visible keyword entry, detached Jobs navigation, exact result cards, full captures, no-results, challenge and missing controls.');
  } finally {
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');
const {readJob} = require('../browser/recommendations-dom.cjs');
const {linkedin, description} = require('./recommendations-fixtures.cjs');

// Current result panes identify the job by its title link and delimit its description with headings.
function modernJob(id, title, location = 'Ithaca, NY') {
  return `<article><header><a href="/company/acme/" aria-label="Company, Acme Systems.">Acme Systems</a>
    <a href="/jobs/view/${id}/">${title}</a><a href="#verified">Verified job</a><p>${location} · 2 weeks ago</p></header>
    <section><div><h2>About the job</h2></div><p>${description}</p></section>
    <section><h2>About the company</h2><p>PRIVATE COMPANY SIDEBAR TEXT</p></section></article>`;
}

// Result cards have no job href; choosing a card reveals the job's own observed URL and title.
function buttonResults(duplicateTitles = false, identified = false) {
  const jobs = [{id: '1', title: duplicateTitles ? 'Production Editor' : 'Copy Editor', location: 'Ithaca, NY'},
    {id: '2', title: duplicateTitles ? 'Production Editor' : 'Layout Editor', location: 'Albany, NY'}];
  const cards = jobs.map(job => `<div role="button" tabindex="0" ${identified ? `componentkey="job-card-component-ref-${job.id}"` : ''}
    style="position:relative;width:600px;height:80px" onclick="selectJob('${job.id}')">
    <span>${job.title}</span><span>Acme Systems</span><span>${job.location}</span>
    <button type="button" style="position:absolute;inset:30%" aria-label="Dismiss ${job.title} job" onclick="event.stopPropagation();fetch('/dismiss')">Dismiss</button></div>`).join('');
  const details = Object.fromEntries(jobs.map(job => [job.id, modernJob(job.id, job.title, job.location)]));
  const titles = Object.fromEntries(jobs.map(job => [job.id, job.title]));
  return `<title>Search | LinkedIn</title><main>${cards}<div id="details"></div></main><script>
    const details=${JSON.stringify(details)},titles=${JSON.stringify(titles)};
    function selectJob(id){setTimeout(()=>{history.replaceState({},'', '/jobs/search-results/?keywords=publishing&currentJobId='+id);
      document.querySelector('#details').innerHTML=details[id];
      document.title=titles[id]+' | Acme Systems | LinkedIn';},id==='2'?700:0);}
    </script>`;
}

// All requests stay on a synthetic site; dismiss and submit destinations reveal wrong control clicks.
async function fixture() {
  const requests = [];
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture.test');
    requests.push(url.pathname);
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    if (url.pathname === '/jobs/') return response.end('<main><h2>Jobs that match your profile</h2><a href="/jobs/view/1/">First job</a><a href="/jobs/view/2/">Second job</a></main>');
    if (url.pathname === '/jobs/search-results/') return response.end(buttonResults(url.searchParams.has('duplicates'), url.searchParams.has('identified')));
    if (/^\/jobs\/view\/[12]\/$/.test(url.pathname)) return response.end(linkedin(false, url.pathname.split('/')[3]));
    response.end('<title>Fixture</title>');
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return {server, requests, origin: `http://127.0.0.1:${server.address().port}`};
}

// Header and description landmarks can change without losing the current job's explicit identity.
async function testModernDescription(session, site) {
  await session.page.goto(site.origin + '/jobs/view/1/');
  await session.page.setContent('<title>Copy Editor | Acme Systems | LinkedIn</title><main>' + modernJob('1', 'Copy Editor') + '</main>');
  const job = await readJob(session.page, 'linkedin', '1', site.origin);
  assert.equal(job.complete, true, JSON.stringify(job));
  assert.equal(job.title, 'Copy Editor');
  assert.equal(job.location, 'Ithaca, NY');
  assert(job.text.includes(description));
  assert(!job.text.includes('PRIVATE COMPANY SIDEBAR'));
  assert.equal((await readJob(session.page, 'linkedin', '999', site.origin)).complete, false);
  const company = modernJob('1', 'Copy Editor').replaceAll('Acme Systems', 'Example Publishing, Inc.')
    .replace(' aria-label="Company, Example Publishing, Inc.."', '');
  await session.page.setContent('<title>Copy Editor | Example Publishing, Inc. | LinkedIn</title><main>' + company + '</main>');
  const punctuated = await readJob(session.page, 'linkedin', '1', site.origin);
  assert.equal(punctuated.complete, true, JSON.stringify(punctuated));
  assert.equal(punctuated.company, 'Example Publishing, Inc.');
}

// Independent observed URLs do not depend on restoring an unrelated feed between captures.
async function testDirectResults(session, site) {
  await session.page.goto(site.origin + '/jobs/');
  const createSession = session.context.newCDPSession.bind(session.context);
  session.context.newCDPSession = async page => {
    const navigation = await createSession(page), send = navigation.send.bind(navigation);
    navigation.send = (method, params) => {
      assert.notEqual(method, 'Page.navigateToHistoryEntry', 'LinkedIn direct captures must not return to the feed.');
      return send(method, params);
    };
    return navigation;
  };
  const result = await dispatch(session, 'source_scan', {limit: 2});
  assert.equal(result.jobs.length, 2, JSON.stringify(result));
  assert(result.jobs.every(job => job.complete), JSON.stringify(result));
}

// Select the card itself, observe its resulting job ID, and retain only the verified description.
async function testButtonResults(session, site) {
  await session.page.goto(site.origin + '/jobs/search-results/?keywords=publishing');
  const result = await dispatch(session, 'source_scan', {limit: 2});
  assert.equal(result.status, 'ready', JSON.stringify(result));
  assert.deepEqual(result.jobs.map(job => job.id), ['1', '2']);
  assert.deepEqual(result.jobs.map(job => job.title), ['Copy Editor', 'Layout Editor']);
  assert(result.jobs.every(job => job.complete && !job.text.includes('PRIVATE COMPANY SIDEBAR')));
  assert(!site.requests.includes('/dismiss'));
  assert(!site.requests.includes('/submit'));
}

// Two cards with equal titles/employers must not reuse the previously selected job while hydrating.
async function testDelayedCardIdentity(session, site) {
  await session.page.goto(site.origin + '/jobs/search-results/?keywords=publishing&duplicates=1');
  const result = await dispatch(session, 'source_scan', {limit: 2});
  assert.equal(result.status, 'ready', JSON.stringify(result));
  assert.deepEqual(result.jobs.map(job => job.id), ['1', '2']);
  assert.deepEqual(result.jobs.map(job => job.location), ['Ithaca, NY', 'Albany, NY']);
  assert(!site.requests.includes('/dismiss'));
}

// An already selected card is bound by its observed component ID even without selected-state ARIA.
async function testObservedCardIdentity(session, site) {
  await session.page.goto(site.origin + '/jobs/search-results/?keywords=publishing&identified=1&currentJobId=1');
  const result = await dispatch(session, 'source_scan', {limit: 2});
  assert.equal(result.status, 'ready', JSON.stringify(result));
  assert.deepEqual(result.jobs.map(job => job.id), ['1', '2']);
  assert(result.jobs.every(job => job.complete));
  assert(!site.requests.includes('/dismiss'));
}

(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-linkedin-results-')), site = await fixture();
  const session = await start({dataRoot: directory, source: 'linkedin', sourceManual: true,
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await testModernDescription(session, site);
    await testDirectResults(session, site);
    await testButtonResults(session, site);
    await testDelayedCardIdentity(session, site);
    await testObservedCardIdentity(session, site);
    console.log('LinkedIn result fixtures passed: modern descriptions, direct captures, observed button cards and exact job identities.');
  } finally {
    await session.context.close();
    site.server.closeAllConnections();
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

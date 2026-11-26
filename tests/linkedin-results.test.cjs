const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start} = require('../browser/worker.cjs');
const {readJob} = require('../browser/recommendations-dom.cjs');
const {fixtureServer, description} = require('./recommendations-fixtures.cjs');

// Current result panes identify the job by its title link and delimit its description with headings.
function modernJob(id, title, location = 'San Jose, CA') {
  return `<article><header><a href="/company/acme/" aria-label="Company, Acme Systems.">Acme Systems</a>
    <a href="/jobs/view/${id}/">${title}</a><a href="#verified">Verified job</a><p>${location} · 2 weeks ago</p></header>
    <section><div><h2>About the job</h2></div><p>${description}</p></section>
    <section><h2>About the company</h2><p>PRIVATE COMPANY SIDEBAR TEXT</p></section></article>`;
}

// Header and description landmarks can change without losing the current job's explicit identity.
async function testModernDescription(session, site) {
  await session.page.goto(site.origin + '/jobs/view/1/');
  await session.page.setContent('<title>Analog Engineer | Acme Systems | LinkedIn</title><main>' + modernJob('1', 'Analog Engineer') + '</main>');
  const job = await readJob(session.page, 'linkedin', '1', site.origin);
  assert.equal(job.complete, true, JSON.stringify(job));
  assert.equal(job.title, 'Analog Engineer');
  assert.equal(job.location, 'San Jose, CA');
  assert(job.text.includes(description));
  assert(!job.text.includes('PRIVATE COMPANY SIDEBAR'));
  assert.equal((await readJob(session.page, 'linkedin', '999', site.origin)).complete, false);
  const company = modernJob('1', 'Analog Engineer').replaceAll('Acme Systems', 'Example Laboratories, Inc.')
    .replace(' aria-label="Company, Example Laboratories, Inc.."', '');
  await session.page.setContent('<title>Analog Engineer | Example Laboratories, Inc. | LinkedIn</title><main>' + company + '</main>');
  const punctuated = await readJob(session.page, 'linkedin', '1', site.origin);
  assert.equal(punctuated.complete, true, JSON.stringify(punctuated));
  assert.equal(punctuated.company, 'Example Laboratories, Inc.');
}

(async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-linkedin-results-')), site = await fixtureServer();
  const session = await start({dataRoot: directory, source: 'linkedin',
    sourceTestOrigin: site.origin, headless: true, allowLocal: true});
  try {
    await testModernDescription(session, site);
    console.log('Saved LinkedIn listing identity and description checks passed.');
  } finally {
    await session.context.close();
    site.server.closeAllConnections();
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

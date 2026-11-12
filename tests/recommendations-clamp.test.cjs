const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start} = require('../browser/worker.cjs');
const {readJob, expandDescription} = require('../browser/recommendations-dom.cjs');
const {fixtureServer, linkedin, description} = require('./recommendations-fixtures.cjs');

// An unused clamp value or an active clamp without overflow does not hide any description text.
async function testUnclipped(page, origin) {
  for (const style of ['-webkit-line-clamp:2', 'display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:100;overflow:hidden']) {
    await page.setContent(linkedin().replace('<p>' + description, '<p style="' + style + '">' + description));
    const job = await readJob(page, 'linkedin', '9005', origin);
    assert.equal(job.complete, true, JSON.stringify(job));
    assert(job.text.includes(description));
  }
}

// The observed native “… more” button expands only its verified job description.
async function testClipped(page, origin) {
  const clamped = '<span id="full" style="display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;width:150px">' + description + '</span>';
  const button = '<button type="button" onclick="document.querySelector(\'#full\').style.webkitLineClamp=\'unset\';this.remove()">… more</button>';
  await page.setContent(linkedin().replace('<p>' + description + '</p>', clamped));
  const clipped = await readJob(page, 'linkedin', '9005', origin);
  assert.equal(clipped.complete, false);
  assert.deepEqual(clipped.issues, ['The job description is still collapsed.']);
  await page.setContent(linkedin().replace('<p>' + description + '</p>', clamped + button));
  assert.equal((await readJob(page, 'linkedin', '9005', origin)).complete, false);
  await expandDescription(page, 'linkedin', '9005', origin);
  const expanded = await readJob(page, 'linkedin', '9005', origin);
  assert.equal(expanded.complete, true, JSON.stringify(expanded));
  assert(expanded.text.includes(description));
  assert.equal(await page.getByRole('button', {name: '… more', exact: true}).count(), 0);
  await page.setContent(linkedin().replace('<p>' + description + '</p>', '<p>' + description + '</p>' + button));
  assert.equal((await readJob(page, 'linkedin', '9005', origin)).complete, false);
}

async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-clamp-'));
  const fixture = await fixtureServer();
  const session = await start({dataRoot: directory, source: 'linkedin', headless: true, allowLocal: true});
  try {
    await session.page.goto(fixture.origin + '/jobs/view/9005/');
    await testUnclipped(session.page, fixture.origin);
    await testClipped(session.page, fixture.origin);
    console.log('Description clamp fixtures passed: inactive/unclipped styles, actual overflow, visible … more, verified expansion.');
  } finally {
    await session.context.close();fixture.server.closeAllConnections();
    await new Promise(resolve => fixture.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
}

main().catch(error => {console.error(error);process.exitCode = 1;});

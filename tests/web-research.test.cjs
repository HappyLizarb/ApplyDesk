const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {start, dispatch} = require('../browser/worker.cjs');

// A normal client-rendered careers page reads job details with a POST request.
async function fixture() {
  const posts = [];
  const description = 'Example Circuits\nHardware Engineer\nResponsibilities\nBuild and validate electronic hardware for lab prototypes.\nRequired qualifications\nBachelor degree in electrical engineering and experience with circuit design.\nCompensation\n$90000/yr';
  const server = http.createServer((req, res) => {
    if (req.method === 'POST') posts.push(req.url);
    if (req.url === '/lookup') return res.end(description);
    if (req.url === '/long') return res.end('<main>' + 'x'.repeat(61000) + '</main>');
    res.setHeader('Content-Type', 'text/html');
    res.end(`<title>Careers</title><main>Loading</main><form action="/submit" method="post"><button>Submit application</button></form>
      <script>fetch('/lookup',{method:'POST',body:'job=example'}).then(r=>r.text()).then(text=>{document.querySelector('main').innerText=text;});</script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, posts, origin: `http://127.0.0.1:${server.address().port}`};
}

// Research allows page rendering while its command surface remains read-only.
async function main() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applydesk-web-test-'));
  const site = await fixture();
  const session = await start({dataRoot: directory, research: true, headless: true, allowLocal: true});
  try {
    const page = await dispatch(session, 'open', {url: site.origin});
    assert.match(page.text, /Required qualifications/);
    assert.match(page.text, /\$90000\/yr/);
    assert.equal(page.text_truncated, false);
    assert.deepEqual(site.posts, ['/lookup']);
    for (const method of ['fill', 'next', 'authenticate', 'submit', 'focus', 'application_entry']) {
      await assert.rejects(dispatch(session, method, {}), /supports page reads/);
    }
    await session.page.locator('button').click();
    await session.page.waitForTimeout(200);
    assert.deepEqual(site.posts, ['/lookup']);
    const long = await dispatch(session, 'open', {url: site.origin + '/long'});
    assert.equal(long.text_truncated, true);
    assert.equal(long.text.length, 60000);
    console.log('Public web research passed: rendered descriptions, read-only commands, exact text and truncation.');
  } finally {
    await dispatch(session, 'close', {});
    await new Promise(resolve => site.server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

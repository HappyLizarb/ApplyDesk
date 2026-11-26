const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const {browserGuard} = require('../browser/guard.cjs');
const {inspect} = require('../browser/inspect.cjs');

// LinkedIn's offscreen zero-size security frame has a client rect despite no visible area.
async function testInvisibleFrame(page, state) {
  await page.setContent('<main>Application fixture</main><iframe title="LinkedIn security verification" aria-hidden="true" src="about:blank" style="position:absolute;left:-9999px;width:0;height:0;border:0"></iframe>');
  assert.equal(await page.locator('iframe').evaluate(frame => frame.getClientRects().length), 1);
  assert.deepEqual((await inspect(page, state)).warnings, []);
}

// Visible authentication and application frames remain unresolved, including misleading aria-hidden frames.
async function testVisibleFrames(page, state) {
  for (const [title, width, height, hidden] of [
    ['Sign in with Google Dialog', 391, 272, false],
    ['Sign in with Google Button', 332, 44, false],
    ['Application form', 600, 500, false],
    ['Application form', 600, 500, true],
    ['Small frame', 1, 1, true],
  ]) {
    await page.setContent(`<main>Application fixture</main><iframe src="about:blank" title="${title}" aria-hidden="${hidden}" style="width:${width}px;height:${height}px;border:0"></iframe>`);
    assert((await inspect(page, state)).warnings.includes('Embedded frames require manual inspection.'), title);
  }
}

// No real platform, sign-in, personal browser or application is accessed.
async function main() {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage(), state = {manual: false, blocked: []};
    await page.evaluate(browserGuard, false);
    await testInvisibleFrame(page, state);
    await testVisibleFrames(page, state);
    console.log('Zero-area frames are excluded; visible sign-in and application frames still require inspection.');
  } finally {await browser.close();}
}
main().catch(error => {console.error(error);process.exitCode = 1;});

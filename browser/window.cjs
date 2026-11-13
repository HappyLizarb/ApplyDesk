const {execFile} = require('node:child_process');
const {promisify} = require('node:util');

// Restore the existing application window without changing its automation or form state.
async function show(session) {
  if (session.options.headless) throw new Error('This browser does not have a visible window.');
  const client = await session.context.newCDPSession(session.page);
  try {
    const {windowId, bounds} = await client.send('Browser.getWindowForTarget');
    if (bounds.windowState === 'minimized') {
      await client.send('Browser.setWindowBounds', {windowId, bounds: {windowState: 'normal'}});
    }
    await session.page.bringToFront();
  } finally {
    await client.detach();
  }
  if (process.platform === 'darwin') await activateProcess(session.context);
  return {visible: true};
}

// Target this browser process so other Chrome profiles stay untouched and no window is created.
async function activateProcess(context) {
  const client = await context.browser().newBrowserCDPSession();
  try {
    const {processInfo} = await client.send('SystemInfo.getProcessInfo');
    const pid = processInfo.find(item => item.type === 'browser')?.id;
    if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('The application browser process is unavailable.');
    const script = `ObjC.import('AppKit'); var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(${pid}); if (!app.activateWithOptions($.NSApplicationActivateIgnoringOtherApps)) throw new Error('Could not show the application window.');`;
    await promisify(execFile)('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {timeout: 5000});
  } finally {
    await client.detach();
  }
}

module.exports = {show};

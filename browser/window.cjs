const {execFile} = require('node:child_process');
const {promisify} = require('node:util');

// Restore the existing application window without changing its automation or form state.
async function show(session, params = {}) {
  if (session.options.headless) throw new Error('This browser does not have a visible window.');
  let bounds = params.bounds;
  if (!bounds && session.options.applicationWorkspace && !session.options.workspacePositioned) {
    const screen = await session.page.evaluate(() => ({left: window.screen.availLeft, top: window.screen.availTop,
      width: window.screen.availWidth, height: window.screen.availHeight}));
    const width = Math.max(320, Math.floor((screen.width - 32) / 2));
    bounds = {left: Math.round(screen.left + screen.width - width - 12), top: Math.round(screen.top + 12),
      width, height: Math.max(320, Math.round(screen.height - 24))};
  }
  if (bounds && (Object.keys(bounds).sort().join() !== 'height,left,top,width' ||
      !Object.values(bounds).every(Number.isSafeInteger) || Math.abs(bounds.left) > 32768 || Math.abs(bounds.top) > 32768 ||
      bounds.width < 320 || bounds.height < 320 || bounds.width > 16384 || bounds.height > 16384)) {
    throw new Error('Choose valid application window bounds.');
  }
  const client = await session.context.newCDPSession(session.page);
  try {
    const {windowId, bounds: current} = await client.send('Browser.getWindowForTarget');
    if (current.windowState === 'minimized' || (bounds && current.windowState !== 'normal')) {
      await client.send('Browser.setWindowBounds', {windowId, bounds: {windowState: 'normal'}});
    }
    if (bounds) await client.send('Browser.setWindowBounds', {windowId, bounds});
    await session.page.bringToFront();
    if (bounds) session.options.workspacePositioned = true;
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

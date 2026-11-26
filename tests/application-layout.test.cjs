const assert = require('node:assert/strict');
const test = require('node:test');

// Exercise the real view and service interface without launching disposable Chrome profiles.
test('only Show moves the workspace, and closing it restores the prior bounds', async () => {
  const {initializeApplicationWindow, updateApplicationWindow} = await import('../web/application-window.js');
  const {workspaceLayout} = await import('../web/application-layout.js');
  const view = {screenX: 55, screenY: 70, outerWidth: 850, outerHeight: 700};
  const original = {...view}, moves = [], requests = [];
  global.window = Object.assign(view, {
    screen: {availLeft: -1440, availTop: 0, availWidth: 1440, availHeight: 900},
    resizeTo(width, height) {moves.push('resize'); Object.assign(view, {outerWidth: width, outerHeight: height});},
    moveTo(x, y) {moves.push('move'); Object.assign(view, {screenX: x, screenY: y});},
    dispatchEvent() {}
  });
  const button = {setAttribute() {}};
  global.document = {createElement: () => button, querySelector: () => ({classList: {toggle() {}}})};
  const panel = {querySelector: id => id === '#assistant-error' ? {after() {}} : button};
  const data = {jobs: [], profile: {confirmed: true}, tasks: [{id: 'show', status: 'done'}]};
  global.fetch = async (path, options) => {
    if (options.method === 'POST') requests.push([path, JSON.parse(options.body)]);
    return {ok: true, json: async () => options.method === 'POST' ? {task: 'show'} : data};
  };
  initializeApplicationWindow(panel);
  for (const paused of [false, true]) {
    updateApplicationWindow(panel, {paused, application_browser: {available: true, workspace_id: 'ordinary'}});
    assert.equal(button.hidden, false);
    assert.deepEqual(moves, []);
    assert.deepEqual(requests, []);
  }
  await button.onclick();
  const {app, browser} = workspaceLayout(view.screen);
  assert.deepEqual(requests, [['/api/orchestrator/show-application', {bounds: browser}]]);
  assert.deepEqual([view.screenX, view.screenY, view.outerWidth, view.outerHeight],
                   [app.left, app.top, app.width, app.height]);
  updateApplicationWindow(panel, {application_browser: {available: false}});
  assert.equal(button.hidden, true);
  for (const [key, value] of Object.entries(original)) assert.equal(view[key], value);
});

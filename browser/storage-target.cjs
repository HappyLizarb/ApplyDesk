const {EventEmitter, once} = require('node:events');

// A private hidden target has no window or tab-strip entry and accepts only internal calls.
class StorageChannel extends EventEmitter {
  constructor(client, sessionId) {
    super();
    this.client = client;this.sessionId = sessionId;this.pending = new Map();this.nextId = 0;
    this.listener = event => this.receive(event);
    client.on('Target.receivedMessageFromTarget', this.listener);
  }

  // Response IDs bind storage operations without logging their sensitive parameters.
  receive(event) {
    if (event.sessionId !== this.sessionId) return;
    const message = JSON.parse(event.message), pending = this.pending.get(message.id);
    if (pending) {
      clearTimeout(pending.timer);this.pending.delete(message.id);
      if (message.error) pending.reject(new Error('Platform storage operation failed.'));
      else pending.resolve(message.result);
    } else if (message.method) this.emit(message.method, message.params);
  }

  // Each call is bounded so an unavailable renderer cannot stall the application worker.
  call(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const fail = () => { clearTimeout(this.pending.get(id)?.timer);this.pending.delete(id);reject(new Error('Platform storage operation failed.')); };
      this.pending.set(id, {resolve, reject, timer: setTimeout(fail, 10000)});
      this.client.send('Target.sendMessageToTarget', {sessionId: this.sessionId,
        message: JSON.stringify({id, method, params})}).catch(fail);
    });
  }

  // Disposing the hidden target removes every outstanding listener and timeout.
  dispose() {
    this.client.removeListener('Target.receivedMessageFromTarget', this.listener);
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);pending.reject(new Error('Platform storage target closed.'));
    }
    this.pending.clear();this.removeAllListeners();
  }
}

// Synthetic blank documents establish storage origins without contacting the platform.
async function restoreOrigin(channel, origin) {
  const controller = new AbortController();
  const loaded = once(channel, 'Page.loadEventFired', {signal: controller.signal});
  loaded.catch(() => {});
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    await channel.call('Page.navigate', {url: origin.origin});
    await loaded;
    const values = JSON.stringify(origin.localStorage), remove = JSON.stringify(origin.remove);
    const result = await channel.call('Runtime.evaluate', {expression:
      `for(const name of ${remove})localStorage.removeItem(name);for(const item of ${values})localStorage.setItem(item.name,item.value);`});
    if (result.exceptionDetails) throw new Error('Platform storage operation failed.');
  } finally { clearTimeout(timer);controller.abort(); }
}

// Only requested platform origins are changed; other jobs keep their live storage untouched.
async function restoreStorage(context, origins) {
  if (!origins.length) return;
  const client = await context.browser().newBrowserCDPSession();
  let targetId, channel;
  try {
    ({targetId} = await client.send('Target.createTarget', {url: 'about:blank', hidden: true, background: true}));
    const {sessionId} = await client.send('Target.attachToTarget', {targetId, flatten: false});
    channel = new StorageChannel(client, sessionId);
    channel.on('Fetch.requestPaused', ({requestId}) => {
      channel.call('Fetch.fulfillRequest', {requestId, responseCode: 200, body: Buffer.from('<html></html>').toString('base64')}).catch(() => {});
    });
    await channel.call('Page.enable');
    await channel.call('Fetch.enable', {patterns: [{urlPattern: '*'}]});
    for (const origin of origins) await restoreOrigin(channel, origin);
  } finally {
    if (channel) channel.dispose();
    if (targetId) await client.send('Target.closeTarget', {targetId}).catch(() => {});
    await client.detach();
  }
}

module.exports = {restoreStorage};

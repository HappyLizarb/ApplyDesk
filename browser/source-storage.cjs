const HOSTS = {linkedin: 'linkedin.com', handshake: 'joinhandshake.com'};
const {restoreStorage} = require('./storage-target.cjs');
const {readKeys, writeKeys} = require('./storage-keys.cjs');

// A source login may be reused only for that platform's own cookie and storage domains.
function platformHost(source, value) {
  const host = HOSTS[source], candidate = String(value || '').replace(/^\./, '').toLowerCase();
  if (!host) throw new Error('Unknown platform sign-in.');
  return candidate === host || candidate.endsWith('.' + host);
}

// Local storage is limited to HTTPS platform origins; credentials never enter job records.
function platformOrigin(source, value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && url.origin === value && platformHost(source, url.hostname);
  } catch { return false; }
}

// Only the private source worker can export its existing platform session in memory.
async function sourceStorage(session) {
  const source = session.state.source;
  if (!HOSTS[source]) throw new Error('A dedicated recommendation session is required.');
  const storage = await session.context.storageState();
  return {cookies: storage.cookies.filter(cookie => platformHost(source, cookie.domain)),
    origins: storage.origins.filter(origin => platformOrigin(source, origin.origin))};
}

// Reject foreign domains before changing any cookies; errors never include submitted secrets.
async function importSource(session, {source, storage}) {
  try {
    if (!HOSTS[source] || !storage || !Array.isArray(storage.cookies) || !Array.isArray(storage.origins)) throw new Error();
    if (storage.cookies.some(cookie => !cookie || !platformHost(source, cookie.domain) || cookie.url)) throw new Error();
    if (storage.origins.some(origin => !platformOrigin(source, origin.origin) || !Array.isArray(origin.localStorage) ||
      origin.localStorage.some(item => typeof item.name !== 'string' || typeof item.value !== 'string'))) throw new Error();
    const metadata = await readKeys(session, source), previous = metadata.previous;
    if ([...previous.keys()].some(origin => !platformOrigin(source, origin))) throw new Error();
    const origins = new Map([...previous].map(([origin, remove]) => [origin, {origin, remove, localStorage: []}]));
    for (const origin of storage.origins) origins.set(origin.origin,
      {origin: origin.origin, remove: previous.get(origin.origin) || [], localStorage: origin.localStorage});
    await restoreStorage(session.context, [...origins.values()]);
    await session.context.clearCookies({domain: new RegExp('(^|\\.)' + HOSTS[source].replace(/\./g, '\\.') + '$')});
    await session.context.addCookies(storage.cookies);
    await writeKeys(metadata, source, storage.origins);
    return {imported: true};
  } catch { throw new Error('Could not reuse the platform sign-in. Sign in in the application browser.'); }
}

module.exports = {sourceStorage, importSource};

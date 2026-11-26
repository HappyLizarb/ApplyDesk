const fs = require('node:fs/promises');
const path = require('node:path');

// Persist only imported key names, so account replacement stays exact across browser restarts.
async function readKeys(session, source) {
  const file = path.join(session.options.dataRoot, 'imported-storage-keys.json');
  let records = {};
  try { records = JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw new Error('Platform storage metadata could not be read.'); }
  const entries = records[source] || [];
  if (!Array.isArray(entries) || entries.some(entry => !Array.isArray(entry) || entry.length !== 2 ||
    typeof entry[0] !== 'string' || !Array.isArray(entry[1]) || entry[1].some(key => typeof key !== 'string'))) {
    throw new Error('Platform storage metadata is invalid.');
  }
  return {file, records, previous: new Map(entries)};
}

// Atomic private metadata contains no cookies, token values, document content, or credentials.
async function writeKeys(metadata, source, origins) {
  metadata.records[source] = origins.map(origin => [origin.origin, origin.localStorage.map(item => item.name)]);
  const temporary = metadata.file + '.tmp';
  await fs.writeFile(temporary, JSON.stringify(metadata.records), {mode: 0o600});
  await fs.rename(temporary, metadata.file);
}

module.exports = {readKeys, writeKeys};

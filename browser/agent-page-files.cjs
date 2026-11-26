const crypto = require('node:crypto');

// Independently hash native browser File bytes without exposing their content to the model.
async function agentFileRecords(page, id) {
  const files = await page.evaluate(async id => {
    const input = globalThis.__applyDesk?.elements.get(id);
    if (!input || input.type !== 'file' || !input.isConnected) throw new Error('The observed file input changed.');
    const files = [...input.files], results = [];
    if (files.reduce((total, file) => total + file.size, 0) > 40 * 1024 * 1024) throw new Error('Attachment verification size exceeded.');
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) throw new Error('Attachment verification size exceeded.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      results.push({name: file.name, size: file.size, type: file.type, base64: btoa(binary)});
    }
    return results;
  }, id);
  return files.map(({base64, ...file}) => ({...file, sha256: crypto.createHash('sha256').update(Buffer.from(base64, 'base64')).digest('hex')}));
}

module.exports = {agentFileRecords};

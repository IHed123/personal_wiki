const fs = require('fs/promises');
const path = require('path');

async function scan(base) {
  const results = [];

  async function walk(dir, prefix = '') {
    let items;
    try {
      items = await fs.readdir(dir, { withFileTypes: true });
    } catch (e) {
      return;
    }

    for (const it of items) {
      const full = path.join(dir, it.name);
      const rel = prefix ? prefix + '/' + it.name : it.name;
      if (it.isDirectory()) {
        await walk(full, rel);
      } else if (it.isFile() && it.name.endsWith('.md')) {
        results.push(rel);
      }
    }
  }

  await walk(base);
  return results;
}

module.exports = { scan };

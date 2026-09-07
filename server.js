const express = require('express');
const path = require('path');
const fs = require('fs/promises');
const indexer = require('./src/indexer');

const app = express();
app.use(express.json());
const FRONTEND_DIR = path.join(__dirname, 'frontend');

app.use(express.static(FRONTEND_DIR));

app.get('/api/files', async (req, res) => {
  try {
    const files = await indexer.scan(process.env.MOUNT_PATH || '/mnt/na');
    res.json(files);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/page/*', async (req, res) => {
  const rel = req.params[0];
  const base = process.env.MOUNT_PATH || '/mnt/na';
  const full = path.join(base, rel);
  try {
    const raw = await fs.readFile(full, 'utf8');
    res.json({ raw });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/page/*', async (req, res) => {
  const rel = req.params[0];
  const content = req.body.content;
  const base = process.env.MOUNT_PATH || '/mnt/na';
  const full = path.join(base, rel);
  try {
    await fs.mkdir(path.dirname(full), { recursive: true });
    await fs.writeFile(full, content, 'utf8');
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/chat', async (req, res) => {
  const { message, host } = req.body;
  if (!host) {
    return res.status(400).json({ error: 'No host provided' });
  }

  try {
    const target = host.startsWith('http') ? host : `http://${host}`;
    const r = await fetch(target, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    const text = await r.text();
    res.json({ response: text });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

const PORT = process.env.PORT || 8082;
app.listen(PORT, () => console.log(`Server listening ${PORT}`));

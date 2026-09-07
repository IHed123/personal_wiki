# IsaWiki — personal file-based markdown wiki (full project snapshot)

This README contains:
- A concise deployment and hosting guide for Ubuntu servers (SSH-only).
- A complete, literal copy of the main source files in this workspace (for auditing or replication by another agent).

Use this file as the single canonical reference for the project. The following files are included verbatim below: `app.py`, `requirements.txt`, `.env.example`, `llm_bridge.py`, `openwebui_adapter.py`, `frontend/index.html`, `frontend/login.html`, `frontend/style.css`, `frontend/app.js`, `frontend/config.js`, `package.json`, `server.js`, `src/indexer.js`, `config.example.json`, `COPILOT_SPEC.md`, `test.md`.

---

## Quick deploy to an Ubuntu server (SSH)

1. Transfer the repo to the server

- If you have a remote Git repo, push then clone on the server:

```bash
# on local: push to remote
# on server:
ssh youruser@yourserver
cd ~
git clone https://your.git.repo.url diy_wiki
cd diy_wiki
```

- If you don't use Git, upload via `rsync` from your machine:

```bash
rsync -avz --exclude .venv --exclude __pycache__ ./ user@yourserver:~/diy_wiki/
```

2. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

3. Create a Python virtualenv, activate it, and install Python deps

```bash
cd ~/diy_wiki
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

4. Configure environment variables and mount your wiki storage

- Copy `.env.example` to `.env` and edit values:

```bash
cp .env.example .env
# edit .env to set WIKI_PATH, PORT, LLM_HOST, SECRET_KEY, etc.
```

- Make sure your external SSD (or mount point) is mounted at `WIKI_PATH` (for example `/mnt/na`). If you need to mount it once:

```bash
sudo mkdir -p /mnt/na
sudo mount /dev/sdX1 /mnt/na   # replace /dev/sdX1 with correct device
```

5. Run under systemd + Gunicorn (recommended)

- Create `/etc/systemd/system/diy_wiki.service` (replace `youruser` paths):

```ini
[Unit]
Description=Isa Wiki (Gunicorn)
After=network.target

[Service]
User=youruser
Group=www-data
WorkingDirectory=/home/youruser/diy_wiki
EnvironmentFile=/home/youruser/diy_wiki/.env
ExecStart=/home/youruser/diy_wiki/.venv/bin/gunicorn -w 4 -b 127.0.0.1:8082 app:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now diy_wiki.service
sudo journalctl -u diy_wiki -f
```

6. Reverse proxy with Nginx (optional, recommended)

- Create `/etc/nginx/sites-available/diy_wiki`:

```nginx
server {
	listen 80;
	server_name example.com;  # replace with your domain or IP

	location / {
		proxy_pass http://127.0.0.1:8082;
		proxy_set_header Host $host;
		proxy_set_header X-Real-IP $remote_addr;
		proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
		proxy_set_header X-Forwarded-Proto $scheme;
	}

	location /static/ {
		alias /home/youruser/diy_wiki/frontend/;
		try_files $uri $uri/ =404;
	}
}
```

```bash
sudo ln -s /etc/nginx/sites-available/diy_wiki /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

7. (Optional) Enable HTTPS with Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
```

8. Troubleshooting

- Service logs:
```bash
sudo journalctl -u diy_wiki -f
```
- Nginx logs:
```bash
sudo tail -f /var/log/nginx/error.log /var/log/nginx/access.log
```
- Check Gunicorn is listening on 127.0.0.1:8082:
```bash
ss -ltnp | grep 8082
```

---

## Full source files (literal contents)

Below are the exact contents of the primary files in this workspace. Use them as a reference or to recreate the project elsewhere.

### app.py
```python
from flask import Flask, jsonify, request, send_from_directory, render_template_string
from flask import session, redirect
from flask_cors import CORS
import os
import markdown
import requests
try:
	from dotenv import load_dotenv
except Exception:
	def load_dotenv():
		return None
from pathlib import Path
from datetime import datetime

load_dotenv()

app = Flask(__name__, static_folder='frontend', static_url_path='')
CORS(app)

# Use environment variable or default mount
WIKI_PATH = os.environ.get('WIKI_PATH', '/mnt/na')
PORT = int(os.environ.get('PORT', 8082))
LLM_HOST = os.environ.get('LLM_HOST')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-please-change')

# Login defaults (can be overridden with env vars)
AUTH_USER = os.environ.get('WIKI_USER', 'ihed')
AUTH_PASS = os.environ.get('WIKI_PASS', '09250610')

# ---------------- FILE LIST ----------------
@app.route('/api/files', methods=['GET'])
def get_files():
	# Backwards-compatible simple file list (array of relative paths)
	files = []
	base = Path(WIKI_PATH)
	if not base.exists():
		return jsonify([])
	for p in base.rglob('*.md'):
		try:
			files.append(p.relative_to(base).as_posix())
		except Exception:
			continue
	files.sort()
	return jsonify(files)


def _extract_title_from_markdown(text):
	# Heuristic: first H1 or first non-empty line
	for line in text.splitlines():
		line = line.strip()
		if not line:
			continue
		if line.startswith('# '):
			return line[2:].strip()
		if line.startswith('#'):
			# other heading levels
			return line.lstrip('#').strip()
		# fallback: first non-empty line shorter than 120 chars
		if len(line) < 120:
			return line
	return None


def _scan_markdown_files(base_path: Path, max_depth=6, include_hidden=False):
	base = Path(base_path)
	items = []
	if not base.exists():
		return items

	for p in base.rglob('*.md'):
		try:
			# depth heuristic
			rel = p.relative_to(base).as_posix()
			depth = len(Path(rel).parts)
			if depth > max_depth:
				continue
			# skip hidden files/dirs when not allowed
			if not include_hidden and any(part.startswith('.') for part in Path(rel).parts):
				continue

			stat = p.stat()
			mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
			title = None
			try:
				with open(p, 'r', encoding='utf-8') as f:
					raw = f.read(4096)
					title = _extract_title_from_markdown(raw)
			except Exception:
				title = None

			# section: use the first path component if present, otherwise root
			parts = Path(rel).parts
			section = parts[0] if len(parts) > 1 else ''

			items.append({
				'path': rel,
				'title': title or Path(rel).stem,
				'section': section,
				'mtime': mtime,
			})
		except Exception:
			continue

	# sort first by section then title
	items.sort(key=lambda x: (x.get('section', ''), x.get('title', '').lower()))
	return items


@app.route('/api/files_meta', methods=['GET'])
def get_files_meta():
	"""Return structured metadata for markdown files: path, title, section, mtime.

	Query params:
	- max_depth (int): limit directory depth scanned (default 6)
	- include_hidden (bool): include dotfiles and dotdirs (default false)
	"""
	base = Path(WIKI_PATH)
	if not base.exists():
		return jsonify([])

	try:
		max_depth = int(request.args.get('max_depth', 6))
	except Exception:
		max_depth = 6
	include_hidden = request.args.get('include_hidden', 'false').lower() in ('1', 'true', 'yes')

	items = _scan_markdown_files(base, max_depth=max_depth, include_hidden=include_hidden)
	return jsonify(items)

# ---------------- PAGE READ ----------------
@app.route('/api/page/<path:filename>', methods=['GET'])
def get_page(filename):
	filepath = os.path.join(WIKI_PATH, filename)
	try:
		with open(filepath, 'r', encoding='utf-8') as f:
			content = f.read()
		html = markdown.markdown(content, extensions=["fenced_code", "tables", "toc"])
		return jsonify({'content': html, 'raw': content})
	except FileNotFoundError:
		return jsonify({'error': 'File not found'}), 404

# ---------------- PAGE SAVE ----------------
@app.route('/api/page/<path:filename>', methods=['POST'])
def save_page(filename):
	filepath = os.path.join(WIKI_PATH, filename)
	data = request.json
	try:
		os.makedirs(os.path.dirname(filepath), exist_ok=True)
		with open(filepath, 'w', encoding='utf-8') as f:
			f.write(data['content'])
		return jsonify({'success': True})
	except Exception as e:
		return jsonify({'error': str(e)}), 500


# ---------------- AUTH (simple session) ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
	if request.method == 'GET':
		# serve login page
		return send_from_directory('frontend', 'login.html')

	# POST -> handle form or JSON
	if request.is_json:
		data = request.get_json() or {}
		username = data.get('username')
		password = data.get('password')
	else:
		username = request.form.get('username')
		password = request.form.get('password')

	if username == AUTH_USER and password == AUTH_PASS:
		session['user'] = username
		if request.is_json:
			return jsonify({'ok': True})
		return redirect('/')

	if request.is_json:
		return jsonify({'error': 'invalid credentials'}), 401
	return send_from_directory('frontend', 'login.html')


@app.route('/logout')
def logout():
	session.pop('user', None)
	return redirect('/login')


@app.before_request
def require_login():
	# Allow login page and static assets (css/js/images)
	allowed_exts = ('.js', '.css', '.png', '.jpg', '.svg', '.ico', '.txt', '.woff2')
	path = request.path
	if path.startswith('/login') or path.startswith('/logout'):
		return None
	if any(path.endswith(ext) for ext in allowed_exts):
		return None

	# If already logged in, allow
	if session.get('user'):
		return None

	# Allow API requests that carry the LLM API key (for bridge/authenticated clients)
	if path.startswith('/api'):
		key = request.headers.get('X-Api-Key') or (request.get_json(silent=True) or {}).get('key')
		api_key = os.environ.get('LLM_API_KEY')
		if api_key and key == api_key:
			return None

	# For browser GET requests, redirect to login page
	if request.method in ('GET', 'HEAD'):
		return redirect('/login')

	# For other requests, return 401
	return jsonify({'error': 'unauthenticated'}), 401


@app.route('/api/page/<path:filename>', methods=['DELETE'])
def delete_page(filename):
	filepath = os.path.join(WIKI_PATH, filename)
	try:
		base = Path(WIKI_PATH).resolve()
		target = Path(filepath).resolve()
		if not str(target).startswith(str(base)):
			return jsonify({'error': 'invalid path'}), 400
		if target.exists():
			target.unlink()
			return jsonify({'success': True})
		else:
			return jsonify({'error': 'not found'}), 404
	except Exception as e:
		return jsonify({'error': str(e)}), 500


@app.route('/api/llm', methods=['POST'])
def api_llm():
	"""Protected endpoint for LLMs to manage wiki files.

	POST JSON: { key: <api_key>, action: 'create'|'update'|'delete'|'append'|'list', path: 'rel/path.md', content: '...' }
	The endpoint checks `LLM_API_KEY` env var (if set) or allows anonymous if not set (NOT recommended).
	"""
	data = request.get_json() or {}
	key = data.get('key') or request.headers.get('X-Api-Key')
	api_key = os.environ.get('LLM_API_KEY')
	if api_key and key != api_key:
		return jsonify({'error': 'forbidden'}), 403

	action = data.get('action')
	rel = data.get('path')
	content = data.get('content', '')
	base = Path(WIKI_PATH).resolve()
	if action == 'list':
		files = []
		for p in base.rglob('*.md'):
			try:
				files.append(p.relative_to(base).as_posix())
			except Exception:
				continue
		files.sort()
		return jsonify({'files': files})

	if not rel:
		return jsonify({'error': 'no path provided'}), 400

	target = (base / rel).resolve()
	if not str(target).startswith(str(base)):
		return jsonify({'error': 'invalid path'}), 400

	try:
		if action in ('create', 'update'):
			target.parent.mkdir(parents=True, exist_ok=True)
			target.write_text(content, encoding='utf8')
			return jsonify({'ok': True})
		elif action == 'append':
			target.parent.mkdir(parents=True, exist_ok=True)
			with open(target, 'a', encoding='utf8') as f:
				f.write(content)
			return jsonify({'ok': True})
		elif action == 'delete':
			if target.exists():
				target.unlink()
				return jsonify({'ok': True})
			return jsonify({'error': 'not found'}), 404
		else:
			return jsonify({'error': 'unknown action'}), 400
	except Exception as e:
		return jsonify({'error': str(e)}), 500

# ---------------- AI CHAT ----------------
@app.route('/api/chat', methods=['POST'])
def ai_chat():
	data = request.json
	user_message = data.get("message", "")
	host = data.get('host') or LLM_HOST
	if not host:
		return jsonify({'error': 'No LLM host configured (provide host in request or set LLM_HOST)'}), 400

	url = host if host.startswith('http') else f'http://{host}'
	try:
		r = requests.post(url, json={'message': user_message}, timeout=30)
		try:
			return jsonify({'response': r.json()})
		except ValueError:
			return jsonify({'response': r.text})
	except Exception as e:
		return jsonify({'error': str(e)}), 500

# ---------------- FRONTEND ----------------
@app.route('/')
def serve_frontend():
	index = Path('frontend') / 'index.html'
	if index.exists():
		return send_from_directory('frontend', 'index.html')
	return render_template_string('<h1>IsaWiki</h1><p>Frontend not found.</p>')

@app.route('/<path:path>')
def serve_static(path):
	return send_from_directory('frontend', path)

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=PORT, debug=True)
```

---

### requirements.txt
```text
Flask>=2.0
flask-cors
markdown
python-dotenv
requests
```

---

### .env.example
```text
# Example environment for IsaWiki
WIKI_PATH=/mnt/na
PORT=8082
LLM_HOST=http://your-llm-host:port
```

---

### llm_bridge.py
```python
"""LLM Bridge Adapter

Runs on the machine that can reach your LLM (OpenWebUI/Ollama). It exposes two endpoints:

- POST /api/chat  -> forwards message to OpenWebUI and returns the LLM response
- POST /api/commit -> commits a change to the wiki by calling the wiki's /api/llm endpoint using configured WIKI_API_KEY

Environment variables:
- OPENWEBUI_BASE (default: http://127.0.0.1:3000)
- WIKI_URL (required for commit, e.g. http://isa-wiki-host:8082)
- WIKI_API_KEY (required for commit)

Run:
	python -m venv .venv
	source .venv/bin/activate
	pip install flask requests
	OPENWEBUI_BASE=http://127.0.0.1:3000 WIKI_URL=http://<isa-wiki>:8082 WIKI_API_KEY=secret python llm_bridge.py

"""
from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

OPENWEBUI_BASE = os.environ.get('OPENWEBUI_BASE', 'http://127.0.0.1:3000')
WIKI_URL = os.environ.get('WIKI_URL')
WIKI_API_KEY = os.environ.get('WIKI_API_KEY')

COMMON_CHAT_ENDPOINTS = ['/api/chat', '/api/generate', '/v1/generate', '/api/textgpt']

def forward_to_openwebui(message):
	for ep in COMMON_CHAT_ENDPOINTS:
		url = OPENWEBUI_BASE.rstrip('/') + ep
		try:
			r = requests.post(url, json={'message': message}, timeout=20)
			if r.status_code == 200:
				try:
					j = r.json()
					# try to pick a good text field
					for k in ('response', 'text', 'generated_text', 'result', 'output'):
						if isinstance(j, dict) and k in j:
							return j[k]
					return j
				except ValueError:
					return r.text
		except Exception:
			continue
	return None

@app.route('/api/chat', methods=['POST'])
def chat():
	data = request.get_json() or {}
	message = data.get('message')
	if not message:
		return jsonify({'error': 'no message'}), 400
	resp = forward_to_openwebui(message)
	if resp is None:
		return jsonify({'error': 'no endpoint succeeded'}), 502
	return jsonify({'response': resp})

@app.route('/api/commit', methods=['POST'])
def commit():
	if not WIKI_URL or not WIKI_API_KEY:
		return jsonify({'error': 'WIKI_URL and WIKI_API_KEY must be set on the bridge'}), 500
	data = request.get_json() or {}
	action = data.get('action')
	path = data.get('path')
	content = data.get('content', '')
	if action not in ('create', 'update', 'append', 'delete'):
		return jsonify({'error': 'invalid action'}), 400
	if not path:
		return jsonify({'error': 'no path provided'}), 400

	url = WIKI_URL.rstrip('/') + '/api/llm'
	payload = {'key': WIKI_API_KEY, 'action': action, 'path': path, 'content': content}
	try:
		r = requests.post(url, json=payload, timeout=20)
		try:
			return jsonify({'status_code': r.status_code, 'response': r.json()})
		except ValueError:
			return jsonify({'status_code': r.status_code, 'response_text': r.text})
	except Exception as e:
		return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5002)))
```

---

### openwebui_adapter.py
```python
"""Small adapter to forward simple `{message:...}` POSTs to an OpenWebUI-compatible endpoint.

Run this on the machine that can reach OpenWebUI (your main PC). Then set IsaWiki's LLM_HOST to this adapter's address (e.g. http://tailscale-host:5001/api/chat).
"""
from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

OPENWEBUI_BASE = os.environ.get('OPENWEBUI_BASE', 'http://127.0.0.1:3000')
# Try common endpoints in order
ENDPOINTS = [
	'/api/chat',
	'/api/generate',
	'/v1/generate',
	'/api/textgpt',
]

@app.route('/api/chat', methods=['POST'])
def chat():
	data = request.get_json() or {}
	message = data.get('message')
	if not message:
		return jsonify({'error': 'no message'}), 400

	for ep in ENDPOINTS:
		url = OPENWEBUI_BASE.rstrip('/') + ep
		try:
			# common shape: {message: ...}
			r = requests.post(url, json={'message': message}, timeout=15)
			if r.status_code == 200:
				try:
					j = r.json()
					# heuristics to find text
					for k in ('response','text','generated_text'):
						if k in j:
							return jsonify({'response': j[k]})
					# some APIs return arrays
					if isinstance(j, dict) and 'results' in j:
						return jsonify({'response': str(j['results'])})
					return jsonify({'response': j})
				except ValueError:
					return jsonify({'response': r.text})
		except Exception:
			continue

	# fallback: try /api/generate with prompt key
	try:
		url = OPENWEBUI_BASE.rstrip('/') + '/api/generate'
		r = requests.post(url, json={'prompt': message}, timeout=15)
		if r.status_code == 200:
			try:
				return jsonify({'response': r.json()})
			except ValueError:
				return jsonify({'response': r.text})
	except Exception as e:
		return jsonify({'error': str(e)}), 500

	return jsonify({'error': 'no endpoint succeeded'}), 502

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))
```

---

### frontend/index.html
```html
<!DOCTYPE html>
<html>
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<title>Isa Wiki</title>
	<link rel="stylesheet" href="style.css">
	<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
	<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
	<script src="config.js"></script>
</head>
<body>

<div id="sidebar">
	<div id="sidebar-header">
		<div style="display:flex;align-items:center;gap:8px">
			<button id="sidebar-toggle" aria-label="Toggle sidebar" style="background:transparent;border:none;font-size:18px;padding:6px;cursor:pointer">☰</button>
			<h2>Isa Wiki</h2>
		</div>
		<button id="settings-btn">⚙️</button>
	</div>

	<input id="search" placeholder="Search files...">
	<ul id="file-tree"></ul>

	<button id="chat-open">AI Chat</button>
</div>

<div id="main">
	<div id="viewer"></div>
	<div id="editor-column">
		<div id="editor-header">File: <span id="current-file">(none)</span>
			<div style="margin-left:auto;display:flex;gap:8px;align-items:center">
                
			</div>
		</div>
		<textarea id="editor" placeholder="Start typing..."></textarea>
	</div>
</div>

<!-- SETTINGS PAGE (full screen) -->
<div id="settings-page">
	<div id="settings-header">
		<button id="settings-back">← Back</button>
		<h2>Settings</h2>
	</div>
	<div id="settings-content">
		<div class="settings-row">
			<label>
				<input type="checkbox" id="toggle-dark">
				Light Mode
			</label>
		</div>

		<div class="settings-row">
			<label>
				<input type="checkbox" id="toggle-autosave" checked>
				Autosave
			</label>
		</div>

		<div class="settings-row">
			<label>
				<input type="checkbox" id="toggle-editing">
				Editing Mode (on = edit; off = preview-only)
			</label>
		</div>

		<div class="settings-row">
			<label>
				<input type="checkbox" id="toggle-split">
				Split view (side-by-side live preview)
			</label>
		</div>

		<div class="settings-row">
			<label>
				Font Size:
				<input type="range" id="font-size" min="12" max="24" value="16">
			</label>
		</div>
		<div class="settings-row">
			<button id="logout-btn" style="background:#fff;border:1px solid #eef2ff;padding:8px 10px;border-radius:8px;cursor:pointer">Log out</button>
		</div>
	</div>
</div>

<!-- AI CHAT PANEL (embedded OpenWebUI) -->
<div id="chat-panel">
	<div id="chat-toolbar">
		<div>OpenWebUI (embedded)</div>
		<div>
			<a id="openwebui-link" href="#" target="_blank" style="color:#ddd;margin-right:8px;">Open in new tab</a>
			<button id="chat-close">Close</button>
		</div>
	</div>
	<iframe id="openwebui-iframe" src="about:blank" style="width:100%;height:100%;border:none;" title="OpenWebUI"></iframe>
</div>

<script src="app.js"></script>
</body>
</html>
```

---

### frontend/login.html
```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IsaWiki — Login</title>
  <link rel="stylesheet" href="style.css">
  <style>
	/* lightweight login overrides to keep file self-contained */
	html,body{height:100%;margin:0}
	body{display:block}
	#login-box { width:360px; max-width:92vw; padding:28px; border-radius:12px; background:#ffffff; box-shadow:0 8px 30px rgba(20,30,60,0.08); color:#111; }
	label { display:block; margin-bottom:8px; color:#111; font-weight:600 }
	input[type=text], input[type=password] { width:100%; padding:10px; margin-bottom:12px; background:#fbfbfd; border:1px solid #eef2ff; color:#111; border-radius:8px }
	button { padding:10px 14px; background:linear-gradient(90deg,#4a7cff,#2aa9ff); color:#fff; border:none; border-radius:8px }
	.login-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; background:linear-gradient(180deg,#fbfdff,#ffffff) }
  </style>
</head>
<body>
  <div class="login-wrap">
	<div id="login-box">
	<h2>IsaWiki Login</h2>
	<form method="POST" action="/login">
	  <label>Username
		<input type="text" name="username" value="ihed">
	  </label>
	  <label>Password
		<input type="password" name="password" value="">
	  </label>
	  <div style="text-align:right">
		<button type="submit">Sign in</button>
	  </div>
	</form>
	</div>
  </div>
</body>
</html>
```

---

### frontend/style.css
```css
body {
	margin: 0;
	display: flex;
	height: 100vh;
	background: #fbfbfc;
	color: #111;
	font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial;
	-webkit-font-smoothing:antialiased;
}

/* Light theme overrides */
/* removed dark mode — light-only design */

#sidebar { background: #ffffff; border-right: 1px solid #eee }
#settings-btn, #new-file, #delete-file { background: #f6f7fb; color:#111 }
#search { background:#fff; color:#111; border:1px solid #eee }
#viewer { background: #fff; color:#111; border-right:1px solid #eee }
#editor { background:#fff; color:#111; border-left:1px solid #eee }

/* SIDEBAR */
#sidebar {
	width: 220px;
	background: #ffffff;
	border-right: 1px solid #eee;
	padding: 18px;
	box-sizing: border-box;
	overflow-y: auto;
	box-shadow: 0 2px 10px rgba(20,20,40,0.04);
}

/* Collapsed sidebar state (desktop) */
body.sidebar-collapsed #sidebar {
	width: 56px;
	padding: 10px 8px;
}
body.sidebar-collapsed #sidebar #file-tree,
body.sidebar-collapsed #sidebar #search,
body.sidebar-collapsed #sidebar #chat-open,
body.sidebar-collapsed #sidebar h2,
body.sidebar-collapsed #sidebar #settings-btn {
	display: none;
}
body.sidebar-collapsed #sidebar #sidebar-header { justify-content: center }
body.sidebar-collapsed #sidebar .sidebar-icon { display:block }

#sidebar { transition: width .18s ease, padding .18s ease }

/* Modern button styles */
#settings-btn, #chat-open {
	background: linear-gradient(90deg,#4a7cff,#2aa9ff);
	color: #fff;
	border: none;
	padding: 8px 12px;
	border-radius: 10px;
	box-shadow: 0 6px 18px rgba(42,105,255,0.12);
	cursor: pointer;
	transition: transform .12s ease, box-shadow .12s ease, opacity .12s ease;
}

#settings-btn:hover, #chat-open:hover { transform: translateY(-3px); box-shadow: 0 10px 28px rgba(42,105,255,0.14); }

/* File list modern styling */
#file-tree { padding: 0; margin: 0; }
#file-tree li { list-style:none; padding:10px 12px; margin-bottom:8px; border-radius:10px; cursor:pointer; transition: all .14s ease; display:flex; align-items:center; justify-content:space-between; border:1px solid transparent }
#file-tree li:hover { background: #f6fbff; transform: translateX(6px); box-shadow: 0 8px 20px rgba(40,60,120,0.06); border-color:#eef6ff }
#file-tree li:active { transform: translateX(2px); }
#file-tree li.selected { background:#eaf2ff; border-color:#cfe6ff; box-shadow: inset 0 0 0 1px rgba(74,124,255,0.05); font-weight:600 }
#file-tree li:focus { outline: 2px solid rgba(74,124,255,0.12); }

/* Viewer/editor outlining */
#viewer { border-radius:10px; border:1px solid #f2f6ff; box-shadow: 0 6px 20px rgba(30,45,90,0.02) }
#editor { border-radius:10px; border:1px solid #f2f6ff; box-shadow: inset 0 1px 0 rgba(255,255,255,0.6) }

/* subtle hover for sidebar header title when expanded */
#sidebar h2 { margin:0; font-size:18px; color:#0f1724 }
#sidebar-toggle { background:transparent; border:none; font-size:18px; cursor:pointer }

#sidebar-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
}

#settings-btn {
	background: #f6f7fb;
	border: none;
	color: #111;
	padding: 8px;
	border-radius: 8px;
	cursor: pointer;
	transition: transform .12s ease, box-shadow .12s ease;
}
#settings-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(16,24,40,0.06) }

#new-file, #delete-file {
	background: #f0f4ff;
	border: none;
	color: #223;
	padding: 8px 10px;
	border-radius: 8px;
	cursor: pointer;
	transition: transform .12s ease;
}
#new-file:hover, #delete-file:hover { transform: translateY(-2px) }

#search {
	width: 100%;
	padding: 10px;
	border-radius: 10px;
	background: #fff;
	color: #111;
	margin: 18px 0;
	box-shadow: inset 0 1px 0 rgba(20,24,40,0.02);
}

/* FILE TREE */
#file-tree li {
	list-style: none;
	padding: 8px;
	cursor: pointer;
	border-radius: 8px;
	transition: background .12s ease, transform .08s ease;
}

#file-tree li:hover {
	background: #f6f7fb;
	transform: translateX(4px);
}

.folder {
	font-weight: bold;
}

/* MAIN */
#main {
	flex: 1;
	display: flex;
	background: linear-gradient(180deg, #fbfdff, #ffffff);
}

/* Responsive: stack sidebar and main on small screens */
@media (max-width: 900px) {
	body { height: auto; flex-direction: column; }
	#sidebar { width: 100%; display:flex; padding:12px; gap:12px; align-items:center; box-shadow: none }
	#sidebar-header h2 { font-size:18px }
	#main { display: block; padding: 12px; }
	#editor-column { width: 100%; }
	#viewer { width: 100%; padding:16px }
	#editor { width: 100%; padding:12px; font-size:14px }
	#file-tree { max-height: 180px; overflow:auto }
	#chat-panel { right: 12px; left: 12px; bottom: 12px; width: auto; height: 50vh; }
}

@media (max-width: 480px) {
	#sidebar { padding:10px }
	#new-file, #delete-file { padding:6px 8px }
	#preview-toggle { padding:6px 8px }
	#editor-header { padding:10px }
	#editor { font-size:13px }
}

#editor-column { display:flex; flex-direction:column; width:70%; transition: all .16s ease }
#editor-header { padding:12px 16px; background: #fff; border-bottom:1px solid #eee; color:#222; display:flex; align-items:center }
#current-file { color:#111; font-weight:600 }
#preview-toggle { background:#eef4ff; border:none; padding:8px 10px; border-radius:8px; cursor:pointer }

/* smooth fade for editor/viewer */
#viewer, #editor { transition: opacity .12s ease, transform .12s ease }

/* VIEWER */
#viewer {
	flex: 1;
	padding: 20px;
	background: #ffffff;
	border-right: 1px solid #f0f3ff;
	overflow-y: auto;
}

/* EDITOR */
#editor {
	flex: 1;
	padding: 20px;
	background: #ffffff;
	color: #111;
	border: none;
	resize: none;
	font-size: 15px;
	font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, 'Roboto Mono', 'Courier New', monospace;
	outline: none;
}

/* SETTINGS MENU */
/* SETTINGS PAGE (fullscreen) */
#settings-page {
	position: fixed;
	inset: 0;
	background: rgba(10,10,10,0.95);
	color: #eee;
	display: none;
	z-index: 50;
	padding: 30px;
	box-sizing: border-box;
}

#settings-header { display:flex; align-items:center; gap:12px; }
#settings-back { background:#222; color:#ddd; border:none; padding:8px 10px; border-radius:6px; cursor:pointer }
#settings-content { margin-top:20px; max-width:820px }
.settings-row { margin-bottom:16px; font-size:16px }

body.light #settings-page { background: rgba(250,250,250,0.98); color: #111 }

/* AI CHAT PANEL */
#chat-panel {
	position: fixed;
	bottom: 24px;
	right: 24px;
	width: 520px;
	height: 420px;
	background: #ffffff;
	border: 1px solid #e8eefc;
	display: none;
	flex-direction: column;
	box-shadow: 0 10px 40px rgba(30,45,90,0.06);
	border-radius: 12px;
	overflow: hidden;
	resize: both;
	min-width: 320px;
	min-height: 240px;
}

/* Chat open button styling */
#chat-open {
	margin-top: 12px;
	width: 100%;
	padding: 10px;
	background: linear-gradient(90deg,#6a5cff,#4ac9ff);
	border: none;
	color: #fff;
	font-weight: 600;
	border-radius: 8px;
	cursor: pointer;
}

body.light #chat-open { background: linear-gradient(90deg,#4a7cff,#2aa9ff); color:#fff }

#chat-messages {
	flex: 1;
	padding: 12px;
	overflow-y: auto;
}

#chat-input { display:none }

/* Chat toolbar */
#chat-toolbar { display:flex; align-items:center; justify-content:space-between; padding:8px 12px; background:#fff; border-bottom:1px solid #f0f3ff; cursor: move }

/* Open in new tab button in toolbar styled */
#openwebui-link { color:#2a6cff; font-weight:600; text-decoration:none }

/* Chat toolbar buttons */
#chat-toolbar button { background:#eef4ff; border:none; padding:6px 10px; border-radius:8px; cursor:pointer }
```

---

### frontend/app.js
```javascript
const API = "/api";

let currentFile = null;
let autosaveEnabled = true;
let autosaveTimer = null;
let editingEnabled = localStorage.getItem('editingEnabled') === 'true';

/* ---------------- FILE TREE ---------------- */
async function loadFiles() {
	const res = await fetch(`${API}/files`);
	const files = await res.json();

	const tree = buildTree(files);
	renderTree(tree, document.getElementById("file-tree"));
}

function buildTree(paths) {
	const root = {};
	paths.forEach(path => {
		const parts = path.split('/');
		let current = root;
		parts.forEach((part, index) => {
			if (!current[part]) {
				current[part] = (index === parts.length - 1) ? null : {};
			}
			current = current[part];
		});
	});
	return root;
}

function renderTree(tree, container, prefix = "") {
	container.innerHTML = "";
	Object.keys(tree).forEach(key => {
		const li = document.createElement("li");
		if (tree[key] === null) {
			li.textContent = key;
			li.dataset.path = prefix + key;
			li.classList.add('file-item');
			li.onclick = (e) => { e.stopPropagation(); loadPage(prefix + key); };
		} else {
			li.textContent = key;
			li.classList.add("folder");

			const subList = document.createElement("ul");
			subList.style.display = "none";

			li.onclick = (e) => {
				e.stopPropagation();
				subList.style.display = subList.style.display === "none" ? "block" : "none";
			};

			renderTree(tree[key], subList, prefix + key + "/");
			li.appendChild(subList);
		}

		container.appendChild(li);
	});
}

function highlightSelected(filename) {
	const items = document.querySelectorAll('#file-tree li');
	items.forEach(li => {
		if (li.dataset && li.dataset.path) {
			if (li.dataset.path === filename) li.classList.add('selected');
			else li.classList.remove('selected');
		}
	});
}

/* ---------------- PAGE LOAD ---------------- */
async function loadPage(filename) {
	currentFile = filename;

	const res = await fetch(`${API}/page/${filename}`);
	const data = await res.json();

	document.getElementById("editor").value = data.raw;
	document.getElementById("viewer").innerHTML = marked.parse(data.raw);
	const cf = document.getElementById('current-file');
	if (cf) cf.textContent = filename;
	// highlight selected file in tree
	try { highlightSelected(filename); } catch (e) {}
}

/* ---------------- PAGE SAVE ---------------- */
async function savePage(filename) {
	const content = document.getElementById("editor").value;

	await fetch(`${API}/page/${filename}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ content })
	});

	document.getElementById("viewer").innerHTML = marked.parse(content);
	// refresh tree
	loadFiles();
}

/* ---------------- NEW / DELETE ---------------- */
const newFileBtn = document.getElementById('new-file');
if (newFileBtn) {
	newFileBtn.onclick = async () => {
		const name = prompt('New file path (relative, e.g. notes/new.md):');
		if (!name) return;
		await savePage(name);
		loadFiles();
		loadPage(name);
	};
}

const deleteFileBtn = document.getElementById('delete-file');
if (deleteFileBtn) {
	deleteFileBtn.onclick = async () => {
		if (!currentFile) return alert('No file selected');
		const ok = confirm('Delete ' + currentFile + '?');
		if (!ok) return;
		const res = await fetch(`${API}/page/${currentFile}`, { method: 'DELETE' });
		if (res.ok) {
			currentFile = null;
			const editorEl = document.getElementById('editor');
			if (editorEl) editorEl.value = '';
			const viewerEl = document.getElementById('viewer');
			if (viewerEl) viewerEl.innerHTML = '';
			const cf = document.getElementById('current-file');
			if (cf) cf.textContent = '(none)';
			loadFiles();
		} else {
			alert('Delete failed');
		}
	};
}

// Logout button in settings
const logoutBtn = document.getElementById('logout-btn');
if (logoutBtn) {
	logoutBtn.onclick = async () => {
		try {
			// call logout and redirect to login page
			await fetch('/logout', { method: 'GET', credentials: 'same-origin' });
		} catch (e) {}
		window.location.href = '/login';
	};
}

// Ctrl+S to save
document.addEventListener('keydown', (e) => {
	if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
		e.preventDefault();
		if (!currentFile) {
			const name = prompt('Save as (relative path):');
			if (!name) return;
			savePage(name).then(() => { loadFiles(); loadPage(name); });
		} else {
			savePage(currentFile);
		}
	}
});

/* ---------------- AUTOSAVE ---------------- */
document.getElementById("editor").addEventListener("input", () => {
	const raw = document.getElementById("editor").value;
	document.getElementById("viewer").innerHTML = marked.parse(raw);

	if (!autosaveEnabled) return;

	clearTimeout(autosaveTimer);
	autosaveTimer = setTimeout(() => {
		if (currentFile) savePage(currentFile);
	}, 1200);
});

/* ---------------- SEARCH ---------------- */
document.getElementById("search").addEventListener("input", (e) => {
	const term = e.target.value.toLowerCase();
	const items = document.querySelectorAll("#file-tree li");

	items.forEach(item => {
		item.style.display = item.textContent.toLowerCase().includes(term) ? "block" : "none";
	});
});

/* ---------------- SETTINGS MENU ---------------- */
document.getElementById("settings-btn").onclick = () => {
	document.getElementById("settings-page").style.display = "block";
};

document.getElementById('settings-back').onclick = () => {
	document.getElementById('settings-page').style.display = 'none';
};

// Light mode toggle: when checked -> light theme
const themeToggle = document.getElementById('toggle-dark');
if (themeToggle) {
	themeToggle.checked = document.body.classList.contains('light');
	themeToggle.onclick = () => {
		document.body.classList.toggle('light');
	};
}

// Autosave default true
const autosaveToggle = document.getElementById('toggle-autosave');
if (autosaveToggle) {
	autosaveToggle.checked = true;
	autosaveEnabled = true;
	autosaveToggle.onchange = (e) => { autosaveEnabled = e.target.checked; };
}

document.getElementById("font-size").oninput = (e) => {
	document.getElementById("editor").style.fontSize = e.target.value + "px";
};

/* ---------------- EDIT MODE ---------------- */
function applyEditingMode() {
	const editorCol = document.getElementById('editor-column');
	const viewer = document.getElementById('viewer');
	const toggle = document.getElementById('toggle-editing');
	if (editingEnabled) {
		editorCol.style.display = 'flex';
		if (toggle) toggle.checked = true;
		viewer.style.flex = '1';
	} else {
		editorCol.style.display = 'none';
		if (toggle) toggle.checked = false;
		viewer.style.flex = '1 1 100%';
	}
}

const editingToggle = document.getElementById('toggle-editing');
if (editingToggle) {
	editingToggle.checked = editingEnabled;
	editingToggle.onchange = (e) => {
		editingEnabled = e.target.checked;
		localStorage.setItem('editingEnabled', editingEnabled ? 'true' : 'false');
		applyEditingMode();
		applySplitMode();
	};
}

applyEditingMode();

/* ---------------- SPLIT VIEW / PREVIEW ---------------- */
let splitView = localStorage.getItem('splitView') === 'true';
const splitToggle = document.getElementById('toggle-split');
if (splitToggle) {
	splitToggle.checked = splitView;
	splitToggle.onchange = (e) => {
		splitView = e.target.checked;
		localStorage.setItem('splitView', splitView ? 'true' : 'false');
		applySplitMode();
	};
}

function applySplitMode() {
	const viewer = document.getElementById('viewer');
	const editor = document.getElementById('editor');
	const editorCol = document.getElementById('editor-column');
	const effectiveSplit = splitView && editingEnabled;
	if (effectiveSplit) {
		// side-by-side editor + live preview
		viewer.style.display = 'block';
		editorCol.style.display = 'flex';
		viewer.style.width = '50%';
		editor.style.width = '50%';
		// ensure editor visible
		editorCol.style.flexDirection = 'column';
	} else {
		// no split: either editing-only or preview-only
		if (editingEnabled) {
			// editing mode: show editor column, hide viewer
			editorCol.style.display = 'flex';
			viewer.style.display = 'none';
			// reset widths
			viewer.style.width = '';
			editor.style.width = '';
		} else {
			// preview-only: hide editor, show viewer full-width
			editorCol.style.display = 'none';
			viewer.style.display = 'block';
			viewer.style.width = '';
		}
	}
}

applySplitMode();

/* OpenWebUI URL is embedded via frontend/config.js */

/* ---------------- AI CHAT (embedded OpenWebUI) ---------------- */
document.getElementById("chat-open").onclick = () => {
	const panel = document.getElementById("chat-panel");
	if (!panel) return;
	const isVisible = window.getComputedStyle(panel).display !== 'none';
	// toggle: hide if visible
	if (isVisible) {
		panel.style.display = 'none';
		return;
	}
	const iframe = document.getElementById('openwebui-iframe');
	if (typeof OPENWEBUI_URL !== 'undefined' && iframe) {
		iframe.src = OPENWEBUI_URL;
		const link = document.getElementById('openwebui-link');
		if (link) link.href = OPENWEBUI_URL;
	}
	// apply saved position/size
	applyChatPanelState();
	panel.style.display = "flex";
};

document.getElementById("chat-close").onclick = () => {
	document.getElementById("chat-panel").style.display = "none";
};

/* ---------------- Chat panel drag/resize persistence ---------------- */
function applyChatPanelState() {
	const panel = document.getElementById('chat-panel');
	if (!panel) return;
	const raw = localStorage.getItem('chatPanel');
	if (!raw) return;
	try {
		const s = JSON.parse(raw);
		if (s.left) panel.style.left = s.left;
		if (s.top) panel.style.top = s.top;
		if (s.width) panel.style.width = s.width + 'px';
		if (s.height) panel.style.height = s.height + 'px';
		// clear right/bottom to allow manual positioning
		panel.style.right = 'auto';
		panel.style.bottom = 'auto';
	} catch (e) {}
}

// drag support
(() => {
	const panel = document.getElementById('chat-panel');
	const toolbar = document.getElementById('chat-toolbar');
	if (!panel || !toolbar) return;
	let dragging = false, ox = 0, oy = 0;

	toolbar.addEventListener('pointerdown', (e) => {
		// ignore pointerdown when clicking toolbar buttons or links so clicks still register
		if (e.target && e.target.closest && e.target.closest('button, a')) return;
		dragging = true;
		const rect = panel.getBoundingClientRect();
		ox = e.clientX - rect.left;
		oy = e.clientY - rect.top;
		try { toolbar.setPointerCapture && toolbar.setPointerCapture(e.pointerId); } catch (err) {}
	});

	document.addEventListener('pointermove', (e) => {
		if (!dragging) return;
		const left = Math.max(8, e.clientX - ox);
		const top = Math.max(8, e.clientY - oy);
		panel.style.left = left + 'px';
		panel.style.top = top + 'px';
		panel.style.right = 'auto';
		panel.style.bottom = 'auto';
	});

	document.addEventListener('pointerup', (e) => {
		if (!dragging) return;
		dragging = false;
		// save
		const rect = panel.getBoundingClientRect();
		const state = { left: rect.left + 'px', top: rect.top + 'px', width: rect.width, height: rect.height };
		localStorage.setItem('chatPanel', JSON.stringify(state));
	});

	// save size on mouseup after resize
	window.addEventListener('mouseup', () => {
		const rect = panel.getBoundingClientRect();
		const state = { left: panel.style.left || rect.left + 'px', top: panel.style.top || rect.top + 'px', width: rect.width, height: rect.height };
		localStorage.setItem('chatPanel', JSON.stringify(state));
	});
})();

/* ---------------- INIT ---------------- */
loadFiles();

// Sidebar toggle: apply saved state and handler
(() => {
	const toggle = document.getElementById('sidebar-toggle');
	const collapsed = localStorage.getItem('sidebarCollapsed') === 'true';
	if (collapsed) document.body.classList.add('sidebar-collapsed');
	if (!toggle) return;
	toggle.onclick = () => {
		const isCollapsed = document.body.classList.toggle('sidebar-collapsed');
		localStorage.setItem('sidebarCollapsed', isCollapsed ? 'true' : 'false');
	};
})();
```

---

### frontend/config.js
```javascript
// Hardcoded OpenWebUI URL — change here if needed
const OPENWEBUI_URL = 'http://127.0.0.1:8080';

// Future: add other hardcoded settings here
```

---

### package.json
```json
{
  "name": "diy-wiki-server",
  "version": "0.1.0",
  "main": "server.js",
  "scripts": {
	"start": "node server.js",
	"dev": "nodemon server.js"
  },
  "dependencies": {
	"express": "^4.18.2",
	"cors": "^2.8.5",
	"chokidar": "^3.5.3"
  }
}
```

---

### server.js (legacy Node prototype)
```javascript
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
```

---

### src/indexer.js
```javascript
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
```

---

### config.example.json
```json
{
  "mountPath": "/mnt/na",
  "port": 8082
}
```

---

### COPILOT_SPEC.md
```text
# ⭐ FULL PROJECT SPEC / MASTER PROMPT FOR GITHUB COPILOT

## Project Name: IsaWiki — Local Markdown‑Based Personal Knowledge System

## Environment: Python Flask backend + static HTML/CSS/JS frontend

## Storage: External SSD mounted on DV6 server (Ubuntu)

## Goal: Build a modern, Obsidian‑style wiki that reads/writes Markdown files directly from the SSD, with AI chat integration.

... (trimmed for brevity in file copy; original is included in workspace) ...
```

---

### test.md
```text
hello1213
```

---

If you would like, I can also:
- Add a machine-readable manifest (full file list) to the README.
- Create a `deploy.sh` that runs the steps above and populates the systemd/nginx configs with your username and domain.
- Produce a `docker-compose.yml` and `Dockerfile` for containerized deployment instead.

Tell me which of the above you want next and I'll create the files. 

After developing IsaWiki on your Mini ITX (with full backend, frontend, and AI integration code ready), we deployed it to your DV6 server to run permanently, accessible from anywhere via Tailscale.

Step 1: Clone Repository from GitHub to DV6

Accessed DV6 via SSH:

bash
ssh ihed@100.120.111.68

Cloned your GitHub repository:

bash
cd ~
git clone https://github.com/IHed123/personal_wiki.git my-wiki-prod
cd my-wiki-prod

This downloaded all your code (Flask backend, HTML/CSS/JavaScript frontend, config files) from GitHub to DV6 at /home/ihed/my-wiki-prod.

Step 2: Install Python Virtual Environment

Problem: DV6 had Python installed, but needed isolated environment to avoid conflicts with system packages.

Installed venv package:

bash
sudo apt install python3-venv

Created virtual environment:

bash
python3 -m venv .venv

This created .venv/ folder containing isolated Python installation.

Activated it:

bash
source .venv/bin/activate

Prompt changed to (.venv) ihed@isaserver:~/my-wiki-prod$ indicating venv was active.

Step 3: Install Python Dependencies

With venv activated, installed all required packages:

bash
pip install -r requirements.txt

Installed:

Flask>=2.0 — Web framework for backend server
flask-cors — Allow cross-origin requests (frontend ↔ backend)
markdown — Convert .md files to HTML for viewing
python-dotenv — Load environment variables from .env file
requests — Make HTTP requests to LLM server (Open WebUI)

All packages installed into .venv/, not system-wide.

Step 4: Configure Environment File

Copied example config:

bash
cp .env.example .env

Edited with nano:

bash
nano .env

Set values (your specific configuration):

WIKI_PATH=/mnt/nas
PORT=8082
LLM_HOST=http://[your-mini-itx-ip]:8080
WIKI_USER=ihed
WIKI_PASS=yourpassword
SECRET_KEY=somethingsecret123

What each does:

WIKI_PATH=/mnt/nas — Point to external SSD where markdown files live
PORT=8082 — Flask listens on port 8082
LLM_HOST — Address of Open WebUI running on your Mini ITX
WIKI_USER / WIKI_PASS — Login credentials for web interface
SECRET_KEY — Security token for session cookies

Saved with Ctrl+X, Y, Enter.

Step 5: Test Flask Application

Started Flask server:

bash
python3 app.py

Output:

Running on http://0.0.0.0:8082

Flask started successfully on DV6.

From your Mini ITX, opened browser:

http://100.120.111.68:8082

Saw:

✓ Login page (with username/password fields)
✓ After login: file tree from /mnt/nas
✓ Could click files to view
✓ Could edit in editor panel
✓ Changes saved to SSD

Verified working, then killed server with Ctrl+C.

Step 6: Create Systemd Service (Auto-Start)

Problem: Flask exits when you disconnect SSH. Need it to run permanently.

Created service file:

bash
sudo nano /etc/systemd/system/my-wiki.service

Pasted service configuration:

ini
[Unit]
Description=Personal Wiki
After=network.target

[Service]
User=ihed
WorkingDirectory=/home/ihed/my-wiki-prod
EnvironmentFile=/home/ihed/my-wiki-prod/.env
ExecStart=/home/ihed/my-wiki-prod/.venv/bin/python3 app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target

What this does:

[Unit] — Describes the service
After=network.target — Start after network is up
User=ihed — Run as user ihed (not root)
WorkingDirectory — Start in project folder
EnvironmentFile — Load variables from .env
ExecStart — Command to run (Python app)
Restart=on-failure — Auto-restart if it crashes
RestartSec=5 — Wait 5 seconds before restarting
WantedBy=multi-user.target — Enable at system boot

Saved with Ctrl+X, Y, Enter.

Step 7: Enable & Start the Service

Reloaded systemd (tell it about new service):

bash
sudo systemctl daemon-reload

Enable on boot:

bash
sudo systemctl enable my-wiki

Start it now:

bash
sudo systemctl start my-wiki

Check status:

bash
sudo systemctl status my-wiki

Output:

● my-wiki.service - Personal Wiki
   Loaded: loaded (/etc/systemd/system/my-wiki.service; enabled; preset: enabled)
   Active: active (running) since [timestamp]

Service running successfully! ✓

Now:

Wiki runs automatically on DV6 boot
Stays running permanently
Auto-restarts if it crashes
Accessible 24/7 at http://100.120.111.68:8082
Step 8: Enable Tailscale Funnel (Worldwide Access)

Made wiki accessible from anywhere (not just local network):

bash
sudo tailscale funnel 8082

Output:

Available on the internet:
https://isaserver.tail745203.ts.net/
|-- proxy http://127.0.0.1:8082

Now accessible from anywhere in the world without port forwarding:

https://isaserver.tail745203.ts.net/
Step 9: Development Workflow
Making Changes on Mini ITX

Edit code in VS Code on your development machine:

app.py — Backend changes
frontend/app.js — JavaScript changes
frontend/style.css — Style changes
etc.
Commit & Push to GitHub

When ready to deploy:

bash
cd C:\Users\isaem\OneDrive\Documents\diy_wiki
git add .
git commit -m "Description of changes"
git push origin main

Code pushed to GitHub with full history/timestamps.

Pull & Deploy on DV6

SSH into DV6:

bash
ssh ihed@100.120.111.68
cd ~/my-wiki-prod
git pull origin main
sudo systemctl restart my-wiki

What happens:

git pull origin main — Downloads latest code from GitHub
sudo systemctl restart my-wiki — Restarts Flask service
New code now running on DV6

For frontend-only changes (HTML/CSS/JS):

Just do git pull → Flask auto-reloads (because debug=True)
No restart needed

For backend changes (Python):

Need systemctl restart to reload code
Architecture After Deployment
Mini ITX (Your development machine)
├── VS Code (edit code)
├── Open WebUI (LLM/AI)
└── Ollama (runs local AI models)
    ↓ (push code to GitHub)
    ↓ 
GitHub Repository (backup & version history)
    ↓ (pull code from GitHub)
    ↓
DV6 Server (Ubuntu, running 24/7)
├── Flask backend (port 8082)
├── Frontend files (HTML/CSS/JS)
└── Reads/writes files to:
    └── External SSD (/mnt/nas)
        ├── projects/
        ├── notes/
        ├── etc.
        └── All your markdown files
    ↑
Access from any browser via:
https://isaserver.tail745203.ts.net/
Summary

You now have a fully deployed personal wiki:

✓ Backend running on DV6 (permanent, auto-restart)
✓ Reads files from external SSD
✓ Web frontend accessible from anywhere
✓ Can edit files through browser
✓ Code backed up on GitHub
✓ Easy to update (push → pull → restart)
✓ AI integration ready (Open WebUI embedded in sidebar)
✓ Login authentication
✓ Auto-starts on server reboot

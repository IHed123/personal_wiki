code of every file: 
app.py:
from flask import Flask, jsonify, request, send_from_directory, render_template_string, Response
from flask import session, redirect
from flask_cors import CORS
import os
import json
import markdown
import requests
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return None
from pathlib import Path
from datetime import datetime

# Load .env and allow overriding existing env vars when present
try:
    load_dotenv(override=True)
except TypeError:
    # older python-dotenv versions may not accept override arg
    load_dotenv()

# If python-dotenv is available, read .env values directly and ensure WIKI_PATH from .env
try:
    from dotenv import dotenv_values
    dotvals = dotenv_values()
    if dotvals and dotvals.get('WIKI_PATH'):
        # prefer .env value and set it in the process env so the rest of the app uses it
        os.environ['WIKI_PATH'] = dotvals.get('WIKI_PATH')
except Exception:
    # If python-dotenv isn't installed, fall back to a tiny manual parser below.
    def _load_dotenv_manually(fn='.env', override=True):
        try:
            p = Path(fn)
            if not p.exists():
                return {}
            vals = {}
            text = p.read_text(encoding='utf-8')
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip()
                # strip surrounding quotes
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                vals[k] = v
                if override or k not in os.environ:
                    os.environ[k] = v
            return vals
        except Exception:
            return {}

    dotvals = _load_dotenv_manually()
    if dotvals.get('WIKI_PATH'):
        os.environ['WIKI_PATH'] = dotvals.get('WIKI_PATH')

app = Flask(__name__, static_folder='frontend', static_url_path='')
CORS(app)

print(f"[IsaWiki] Effective WIKI_PATH={os.environ.get('WIKI_PATH')} (cwd={os.getcwd()})")

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
    # Heuristic: return the first Markdown heading (any level, e.g. '#', '##').
    # Do NOT use the first non-empty line as a title because that often captures
    # content lines and causes file listings to show content instead of filenames.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            # strip leading '#' characters and surrounding whitespace
            return line.lstrip('#').strip()
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

            # Use filename (without extension) as title always. Avoid using
            # file content heuristics so the UI consistently shows filenames.
            parts = Path(rel).parts
            section = parts[0] if len(parts) > 1 else ''
            items.append({
                'path': rel,
                'title': Path(rel).stem,
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


@app.route('/api/scan_status', methods=['GET'])
def scan_status():
    """Simple debug endpoint returning mount path info and a small sample of markdown files."""
    base = Path(WIKI_PATH)
    exists = base.exists()
    sample = []
    count = 0
    if exists:
        items = _scan_markdown_files(base, max_depth=6, include_hidden=False)
        count = len(items)
        sample = items[:10]
    return jsonify({
        'WIKI_PATH': WIKI_PATH,
        'exists': exists,
        'count': count,
        'sample': sample,
    })

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
        # After login, send the user to the protected SPA entrypoint.
        return redirect('/app')

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
    # Allow a few read-only diagnostic/file-list endpoints without login
    if path.startswith('/api/scan_status') or path.startswith('/api/files_meta') or path.startswith('/api/files'):
        return None

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


@app.route('/config.js')
def serve_config_js():
    """Dynamically generate a small client-side config script from environment vars.

    Only expose non-secret, frontend-safe settings here.
    """
    cfg = {
        'API_BASE': os.environ.get('API_BASE', '/api'),
        'OPENWEBUI_URL': os.environ.get('OPENWEBUI_URL', 'http://127.0.0.1:8080'),
    }
    safe_cfg = {k: v for k, v in cfg.items() if v is not None and v != ''}
    js = 'window.CONFIG = ' + json.dumps(safe_cfg) + ';'
    return Response(js, mimetype='application/javascript')

# ---------------- FRONTEND ----------------
@app.route('/')
def serve_frontend():
    # Always show the login page when the root URL is requested.
    # The actual single-page app is served from `/app` after a successful login.
    return redirect('/login')


@app.route('/app')
def serve_app():
    # Protected SPA entrypoint. Only serve when logged in.
    index = Path('frontend') / 'index.html'
    if index.exists():
        return send_from_directory('frontend', 'index.html')
    return render_template_string('<h1>IsaWiki</h1><p>Frontend not found.</p>')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('frontend', path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=True)

.env:
# Local .env - created from .env.example
# Edit values here; this file is gitignored.

WIKI_PATH=C:/Users/isaem/OneDrive/Documents/diy_wiki
PORT=8082
OPENWEBUI_URL=http://127.0.0.1:8080
LLM_HOST=
LLM_API_KEY=
WIKI_USER=ihed
WIKI_PASS=09250610
SECRET_KEY=replace-me
API_BASE=/api

index.html:
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
            <label>
                OpenWebUI URL:
                <input id="openwebui-url" type="text" placeholder="http://127.0.0.1:8080" style="width:100%;padding:8px;border-radius:8px;border:1px solid #e6eefc;margin-top:6px">
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
style.css:
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
app.js:
const API = "/api";

let currentFile = null;
let autosaveEnabled = true;
let autosaveTimer = null;
let editingEnabled = localStorage.getItem('editingEnabled') === 'true';

/* ---------------- FILE TREE ---------------- */
async function loadFiles() {
    // Prefer the richer metadata endpoint; fall back to simple list
    let items = [];
    try {
        const res = await fetch(`${API}/files_meta`);
        if (res.ok) items = await res.json();
    } catch (e) {
        // ignore
    }

    if (!items || items.length === 0) {
        // fallback to old endpoint (just paths)
        try {
            const r2 = await fetch(`${API}/files`);
            const files = await r2.json();
            items = files.map(p => ({ path: p, title: p.split('/').pop(), section: p.split('/').length>1 ? p.split('/')[0] : '' }));
        } catch (e) {
            items = [];
        }
    }

    renderSections(items, document.getElementById('file-tree'));
}


function renderSections(items, container) {
    container.innerHTML = '';
    // group by section
    const groups = {};
    items.forEach(it => {
        const sec = it.section || 'root';
        if (!groups[sec]) groups[sec] = [];
        groups[sec].push(it);
    });

    const orderedSections = Object.keys(groups).sort((a,b) => {
        if (a === 'root') return 1;
        if (b === 'root') return -1;
        return a.localeCompare(b);
    });

    orderedSections.forEach(sec => {
        const header = document.createElement('li');
        header.classList.add('folder');
        header.textContent = (sec === 'root') ? 'Other / Root' : sec;

        const subList = document.createElement('ul');
        subList.style.display = 'block';
        groups[sec].forEach(it => {
            const li = document.createElement('li');
            li.textContent = it.title || it.path.split('/').pop();
            li.dataset.path = it.path;
            li.classList.add('file-item');
            li.title = it.path;
            li.onclick = (e) => { e.stopPropagation(); loadPage(it.path); };
            subList.appendChild(li);
        });

        // allow collapsing
        header.style.cursor = 'pointer';
        header.onclick = (e) => {
            e.stopPropagation();
            subList.style.display = subList.style.display === 'none' ? 'block' : 'none';
        };

        container.appendChild(header);
        container.appendChild(subList);
    });
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

// OpenWebUI settings: load/save URL from Settings input
const openwebuiInput = document.getElementById('openwebui-url');
if (openwebuiInput) {
    const existing = localStorage.getItem('OPENWEBUI_URL') || (typeof OPENWEBUI_URL !== 'undefined' ? OPENWEBUI_URL : '');
    openwebuiInput.value = existing;
    openwebuiInput.onchange = (e) => {
        const v = e.target.value.trim();
        if (v) localStorage.setItem('OPENWEBUI_URL', v);
        else localStorage.removeItem('OPENWEBUI_URL');
    };
}

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
    // Prefer a user-set URL in localStorage, otherwise fall back to config.js OPENWEBUI_URL
    let url = localStorage.getItem('OPENWEBUI_URL');
    if (!url && typeof OPENWEBUI_URL !== 'undefined') url = OPENWEBUI_URL;
    if (url && iframe) {
        iframe.src = url;
        const link = document.getElementById('openwebui-link');
        if (link) link.href = url;
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
login.html:
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
theres also a requirements.txt
.venv folder and a _pycache_ folder
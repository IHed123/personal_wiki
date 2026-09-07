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
                'title': rel,
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

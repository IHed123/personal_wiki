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

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

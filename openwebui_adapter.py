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

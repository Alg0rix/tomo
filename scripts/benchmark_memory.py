"""Measure Linux server RSS with isolated state and a local fake LLM.

Run with the project interpreter: .venv/bin/python scripts/benchmark_memory.py.
Reports decimal MB, including the process high-water mark. The fake provider
lives in this measuring process and is excluded from Tomo's server RSS.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Provider(BaseHTTPRequestHandler):
    tool_calls = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"memory-bench","context_window":32768}]}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = payload.get('messages', [])
        # Exercise a real local tool once per agent turn (background title and
        # memory requests have no tools, so receive plain completions).
        last_role = next((m['role'] for m in reversed(messages) if m['role'] != 'system'), '')
        wants_tool = bool(payload.get('tools')) and last_role == 'user' 
        if wants_tool:
            type(self).tool_calls += 1
            delta = {'tool_calls': [{'index': 0, 'id': 'call_bench', 'type': 'function',
                     'function': {'name': 'list_dir', 'arguments': '{"path":"."}'}}]}
            message = dict(delta, content=None)
        else:
            delta = {'content': 'Memory benchmark complete.'}
            message = {'role': 'assistant', **delta}
        usage = {'prompt_tokens': 32, 'completion_tokens': 8}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if payload.get('stream') else 'application/json')
        self.end_headers()
        if payload.get('stream'):
            for event in ({'choices': [{'delta': delta}]}, {'choices': [], 'usage': usage}):
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.wfile.write(json.dumps({'choices': [{'message': message}], 'usage': usage}).encode())


def memory(pid: int) -> dict:
    values = {}
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmHWM:')):
            values[line.split(':')[0]] = int(line.split()[1]) * 1024
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turns', type=int, default=10)
    parser.add_argument('--limit-mb', type=float, default=80, help='RSS budget at measured checkpoints (decimal MB)')
    args = parser.parse_args()
    if args.turns < 1 or args.limit_mb <= 0:
        parser.error('turns and limit-mb must be positive')
    if not Path('/proc/self/status').exists():
        parser.error('this benchmark needs Linux /proc')
    measurements = []
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='tomo-memory-') as root:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, TOMO_HOME=root+'/home', TOMO_WORK=root+'/work',
                       TOMO_DB_PATH=root+'/home/state/tomo.db', TOMO_VAR_DIR=root+'/home/state',
                       TOMO_SKILLS_EXTERNAL_DIRS='', TOMO_ADMIN_PASSWORD='memory-bench-pass',
                       TOMO_RELOAD='false', TOMO_HOST='127.0.0.1', TOMO_PORT=str(port),
                       TOMO_COOKIE_SECURE='false')
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            base = f'http://127.0.0.1:{port}'

            def request(path, body=None, *, method=None, form=False):
                data = None if body is None else (urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode())
                req = urllib.request.Request(base+path, data=data, method=method,
                      headers={'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'})
                with client.open(req, timeout=60) as response:
                    return response.read()

            with open(root+'/server.log', 'w+') as log:
                process = subprocess.Popen([sys.executable, '-m', 'app.main'], cwd=ROOT, env=env, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            request('/login')
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                raise RuntimeError('Server did not become ready:\n'+log.read())
                            time.sleep(.1)

                    def measure(stage):
                        result = dict(stage=stage, **memory(process.pid))
                        measurements.append(result)
                        print(json.dumps(result), flush=True)

                    measure('ready')
                    request('/login', {'username': 'admin', 'password': 'memory-bench-pass'}, form=True)
                    for path in ('/', '/api/agents', '/api/sessions', '/system', '/memory'):
                        request(path)
                    measure('pages_after_login')
                    request('/api/llm-profiles', {'id': 'bench', 'name': 'Bench', 'api_key': 'bench-key',
                            'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'model': 'memory-bench'})
                    request('/api/llm-profiles/bench/default', {}, method='POST')
                    for turn in range(args.turns):
                        raw = request('/v1/chat/completions', {'model': 'main', 'stream': True,
                              'messages': [{'role': 'user', 'content': f'List the current directory, benchmark {turn}.'}]})
                        if b'Memory benchmark complete.' not in raw or b'[DONE]' not in raw:
                            raise RuntimeError(f'Chat turn {turn} did not complete: {raw[:1000]!r}')
                        measure(f'chat_{turn+1}')
                    # Login again after the HTTP clients and turn machinery are warm.
                    request('/login', {'username': 'admin', 'password': 'memory-bench-pass'}, form=True)
                    measure('login_after_chat')
                    if Provider.tool_calls < args.turns:
                        raise RuntimeError(f'Expected {args.turns} tool rounds; got {Provider.tool_calls}')
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join()
    peak = max(item['VmHWM'] for item in measurements)
    rss = max(item['VmRSS'] for item in measurements)
    passed = rss < args.limit_mb * 1_000_000
    print(json.dumps({'max_checkpoint_rss_mb': round(rss/1_000_000, 3),
                      'peak_rss_mb': round(peak/1_000_000, 3), 'tool_rounds': Provider.tool_calls,
                      'limit_mb': args.limit_mb, 'passed': passed}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())

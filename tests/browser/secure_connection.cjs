/* NODE_PATH=<existing Playwright>/node_modules node tests/browser/secure_connection.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-secure-http-'));
  // Only the external LLM is replaced. UI, chat loop, bash, CLI, store, broker
  // and local upstream all use their actual production paths.
  const python = `
import json, os, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import uvicorn
sock = socket.socket(); sock.bind(('127.0.0.1', 0))
os.environ['TOMO_PORT'] = str(sock.getsockname()[1])
from app.main import app
from app.services import store
from app.runtime.llm.mock import MockLLMClient
import app.runtime.agent.loop as loop
class BrowserLLM(MockLLMClient):
    async def complete(self, messages, tools=None):
        response = await super().complete(messages, tools)
        for call in response.tool_calls or []:
            if call.name == 'bash': call.arguments['timeout'] = 120
        return response
loop.get_llm = lambda agent_id=None: BrowserLLM()
user = store.create_user({'username': 'alice', 'password': 'browser-test-password'})
sid = store.create_swarm_session(['main'], user_id=user['id'])
class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{"result":[{"hostid":"42"}]}')
upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
threading.Thread(target=upstream.serve_forever, daemon=True).start()
print(json.dumps({'port': int(os.environ['TOMO_PORT']), 'sid': sid, 'upstream': upstream.server_port}), flush=True)
uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='critical', access_log=False)).run(sockets=[sock])
`;
  const child = spawn(path.join(root, '.venv/bin/python'), ['-c', python], {
    cwd: root,
    env: { ...process.env, TOMO_HOME: home, TOMO_DB_PATH: path.join(home, 'test.db'),
      TOMO_WORK: path.join(home, 'work'), TOMO_HOST: '127.0.0.1',
      TOMO_SECRET_KEY: '', TOMO_SESSION_SECRET: 'synthetic-browser-session-key-not-production',
      TOMO_ADMIN_PASSWORD: 'synthetic-admin-password-not-production' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stderr.on('data', chunk => { logs += chunk; });
  let browser;
  try {
    const meta = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Server startup timed out')), 15000);
      readline.createInterface({ input: child.stdout }).on('line', line => {
        try { const value = JSON.parse(line); if (value.port) { clearTimeout(timer); resolve(value); } } catch (_) {}
      });
      child.on('exit', code => { clearTimeout(timer); reject(new Error('Server exited: ' + code + '\n' + logs)); });
    });
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    const base = 'http://127.0.0.1:' + meta.port;
    await page.goto(base + '/login');
    await page.locator('[name=username]').fill('alice');
    await page.locator('[name=password]').fill('browser-test-password');
    await page.locator('button[type=submit]').click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'));
    await page.request.put(base + '/api/sessions/' + meta.sid + '/approval-mode', { data: { mode: 'off' } });
    await page.goto(base + '/sessions?s=' + meta.sid);
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.chatInit === '1');
    const cli = path.join(root, '.venv/bin/tomo');
    const send = async command => {
      await page.locator('.chat-input').fill('run: ' + cli + ' ' + command);
      await page.locator('.chat-input').press('Enter');
    };
    await send('connection request zabbix --url http://127.0.0.1:' + meta.upstream);
    const dialog = page.locator('dialog.connection-dialog[open]');
    await dialog.waitFor({ timeout: 20000 });
    const shot = path.join(process.env.BB_THREAD_STORAGE || home, 'secure-connection.png');
    await page.screenshot({ path: shot });
    // Failed validation stays actionable and does not echo credential input.
    const secret = 'synthetic-browser-zabbix-token';
    await dialog.locator('[name=secret]').fill(secret);
    await dialog.locator('button[type=submit]').click();
    await dialog.locator('.connection-error').waitFor({ state: 'visible' });
    assert.doesNotMatch(await dialog.textContent(), new RegExp(secret));
    // Refresh restores pending metadata, not password input, while bash continues.
    await page.reload();
    await page.getByRole('button', { name: 'Enter private values' }).click();
    assert.equal(await dialog.locator('[name=secret]').inputValue(), '');
    await dialog.locator('[name=secret]').fill(secret);
    await dialog.locator('[name=allow_http]').check();
    await dialog.locator('button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('.composer').classList.contains('is-generating'));
    assert.equal(await page.locator('dialog.connection-dialog').count(), 0);
    let history = await (await page.request.get(base + '/api/sessions/' + meta.sid + '/chat')).text();
    assert.ok(!history.includes(secret), 'Credential must not enter persisted history');
    await send(`http --connection zabbix -X POST /api_jsonrpc.php -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","method":"host.get","params":{},"id":1}'`);
    await page.waitForFunction(() => document.querySelector('.chat-scroll').textContent.includes('hostid'));
    await page.waitForFunction(() => !document.querySelector('.composer').classList.contains('is-generating'));
    history = await (await page.request.get(base + '/api/sessions/' + meta.sid + '/chat')).text();
    assert.ok(history.includes('42') && !history.includes(secret));
    await send('connection request cancelled --url http://127.0.0.1:' + meta.upstream);
    await dialog.waitFor();
    await dialog.getByRole('button', { name: 'Cancel request' }).click();
    await page.waitForFunction(() => !document.querySelector('.composer').classList.contains('is-generating'));
    assert.equal(await page.locator('dialog.connection-dialog').count(), 0);
    // Generic form: arbitrary names/types, no connection, URL or auth scheme.
    const schema = {
      title: 'Private signing configuration', purpose: 'Store operator-provided configuration privately',
      fields: [
        { name: 'WORKSPACE', label: 'Workspace name', type: 'text', description: 'This text value is private too' },
        { name: 'KEY_PASSWORD', label: 'Key passphrase', type: 'password' },
        { name: 'SIGNING_KEY', label: 'Signing material', type: 'textarea' },
        { name: 'ENVIRONMENT', label: 'Environment', type: 'select', options: ['sandbox', 'production'] },
      ],
    };
    await send("secret request signing --form '" + JSON.stringify(schema) + "'");
    await dialog.waitFor();
    assert.equal(await dialog.locator('[data-private-field]').count(), 4);
    assert.equal(await dialog.locator('[data-config=base_url]').count(), 0);
    assert.equal(await dialog.locator('h3').textContent(), schema.title);
    const dynamicShot = path.join(process.env.BB_THREAD_STORAGE || home, 'dynamic-secrets.png');
    await page.screenshot({ path: dynamicShot });
    const privateText = 'synthetic-private-workspace';
    const privatePassword = 'synthetic-signing-passphrase';
    const privateMaterial = 'synthetic multiline\\nprivate signing material\\n';
    await dialog.locator('[name=WORKSPACE]').fill(privateText);
    await dialog.locator('[name=KEY_PASSWORD]').fill(privatePassword);
    await dialog.locator('[name=SIGNING_KEY]').fill(privateMaterial);
    await dialog.locator('[name=ENVIRONMENT]').selectOption('production');
    await dialog.locator('button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('.composer').classList.contains('is-generating'));
    assert.equal(await page.locator('dialog.connection-dialog').count(), 0);
    history = await (await page.request.get(base + '/api/sessions/' + meta.sid + '/chat')).text();
    const metadata = await (await page.request.get(base + '/api/sessions/' + meta.sid + '/secrets')).text();
    for (const value of [privateText, privatePassword, privateMaterial, secret]) {
      assert.ok(!history.includes(value) && !metadata.includes(value) && !logs.includes(value));
    }
    const bundles = JSON.parse(metadata).bundles;
    const signing = bundles.find(bundle => bundle.name === 'signing');
    assert.deepEqual(signing.usage, {});
    assert.ok(!Object.hasOwn(signing, 'values'));
    assert.deepEqual(errors, []);
    assert.ok(!logs.includes(secret), 'Credential must not enter server logs');
    console.log('PASS: dynamic private fields (text/password/textarea/select), store-only flow, refresh, CLI HTTP, cancellation, no private values in history/logs');
    console.log('Dynamic screenshot: ' + dynamicShot);
    console.log('Screenshot: ' + shot);
  } finally {
    if (browser) await browser.close();
    if (child.exitCode === null) {
      child.kill('SIGTERM');
      await new Promise(resolve => child.once('exit', resolve));
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

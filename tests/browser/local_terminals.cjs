/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/local_terminals.cjs
 * Optional: CHROME_PATH, TOMO_BROWSER_ARTIFACTS. Uses a disposable Tomo home/DB.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-terminal-browser-'));
  const artifacts = process.env.TOMO_BROWSER_ARTIFACTS || process.env.BB_THREAD_STORAGE || home;
  fs.mkdirSync(artifacts, { recursive: true });
  const python = `
import json, socket
import uvicorn
from app.main import app
from app.services import store
from app.runtime.artifacts.fs import write_artifact_text
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
user = store.create_user({'username': 'terminal_preview', 'password': 'preview_password1'})
sessions = [store.create_swarm_session(['main'], user_id=user['id']) for _ in range(2)]
for sid, title in zip(sessions, ['Terminal workspace', 'Separate chat']):
    store.set_session_title(sid, title)
    store.append_session_history(sid, {'type': 'user', 'content': title, 'ts': 1.0})
write_artifact_text(sessions[0], 'workspace-notes.md', '# Workspace notes\\n\\nLocal terminals stay with this chat.\\n')
write_artifact_text(sessions[0], 'results.csv', 'terminal,status\\nbash 1,ready\\n')
print(json.dumps({'port': sock.getsockname()[1], 'sessions': sessions}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False)).run(sockets=[sock])
`;
  const child = spawn(process.env.TOMO_TEST_PYTHON || path.join(root, '.venv/bin/python'), ['-c', python], {
    cwd: root,
    env: { ...process.env, TOMO_HOME: home, TOMO_DB_PATH: path.join(home, 'state/test.db'),
      TOMO_WORK: path.join(home, 'work'), TOMO_SKILLS_EXTERNAL_DIRS: '',
      TOMO_SECRET_KEY: '', TOMO_ADMIN_PASSWORD: 'synthetic-browser-admin-password' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '';
  child.stderr.on('data', chunk => { logs = (logs + chunk).slice(-16000); });
  let browser, page;
  try {
    const meta = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Server startup timed out\n' + logs)), 20000);
      readline.createInterface({ input: child.stdout }).on('line', line => {
        try {
          const value = JSON.parse(line);
          if (value.port) { clearTimeout(timer); resolve(value); }
        } catch (_) {}
      });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Server exited: ' + code + '\n' + logs)); });
    });
    const base = 'http://127.0.0.1:' + meta.port;
    // Metadata precedes ASGI startup; wait for the app's actual HTTP response.
    const deadline = Date.now() + 20000;
    while (true) {
      try {
        const response = await fetch(base + '/login', { signal: AbortSignal.timeout(1000) });
        if (response.ok) break;
      } catch (_) {}
      if (Date.now() >= deadline || child.exitCode !== null) throw new Error('Server did not become ready\n' + logs);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const sids = meta.sessions;
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
    page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/login');
    await page.locator('input[name=username]').fill('terminal_preview');
    await page.locator('input[name=password]').fill('preview_password1');
    await page.locator('button[type=submit]').click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'));
    await page.goto(base + '/sessions?s=' + sids[0]);
    await page.waitForFunction(sid => document.querySelector('.chat-wrap')?.dataset.sessionId === sid &&
      document.querySelector('.chat-wrap')?.dataset.chatInit === '1', sids[0]);
    await page.locator('.cap-expand-strip').click();
    await page.locator('[data-workspace-view=terminal]').click();
    await page.getByRole('button', { name: 'Open terminal', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-terminal-status]')?.textContent === 'Shell ready');
    await page.keyboard.type("export UI_VALUE=preserved; printf '__%s__\\n' first");
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__first__'));
    const before = await page.evaluate(async sid => (await Tomo.api('/api/sessions/' + sid + '/terminals')).terminals, sids[0]);
    assert.equal(before.length, 1);
    await page.locator('[data-terminal-create]').first().click();
    await page.waitForFunction(() => document.querySelectorAll('.terminal-tab').length === 2 &&
      document.querySelector('[data-terminal-status]')?.textContent === 'Shell ready');
    await page.keyboard.type("printf '__%s__\\n' second");
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__second__'));
    await page.screenshot({ path: path.join(artifacts, 'workspace-terminal-desktop.png') });

    // Real rail navigation reuses the chat DOM; shell state stays with its owner.
    await page.locator('.session-item[data-id="' + sids[1] + '"]').click();
    await page.getByRole('button', { name: 'Open terminal', exact: true }).waitFor();
    assert.equal(await page.locator('.terminal-tab').count(), 0);
    await page.getByRole('button', { name: 'Open terminal', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-terminal-status]')?.textContent === 'Shell ready');
    await page.keyboard.type("printf '__other:%s__\\n' \"${UI_VALUE:-empty}\"");
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__other:empty__'));
    await page.locator('.session-item[data-id="' + sids[0] + '"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.terminal-tab').length === 2);
    await page.locator('[data-terminal-select]').first().click();
    await page.keyboard.type("printf '__value:%s__\\n' \"$UI_VALUE\"");
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__value:preserved__'));

    await page.locator('[data-workspace-view=home]').click();
    assert.equal(await page.locator('.xterm-screen').count(), 0);
    await page.locator('[data-cap-file="workspace-notes.md"]').waitFor();
    await page.screenshot({ path: path.join(artifacts, 'workspace-files-desktop.png') });
    await page.locator('[data-cap-file="workspace-notes.md"]').click();
    await page.locator('.cap-drill-body h1').waitFor();
    await page.screenshot({ path: path.join(artifacts, 'workspace-preview-desktop.png') });
    await page.locator('[data-workspace-view=terminal]').click();
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__value:preserved__'));
    assert.match(await page.locator('[data-terminal-idle]').textContent(), /Idle close in \d+:\d+/);
    // A background artifact must not interrupt an active terminal.
    await page.evaluate(sid => TomoArtifacts.maybeAutoOpen({ session_id: sid,
      filename: 'workspace-notes.md', url: '/api/sessions/' + sid + '/artifacts/workspace-notes.md' }), sids[0]);
    assert.equal(await page.locator('[data-workspace-view=terminal]').getAttribute('aria-pressed'), 'true');

    await page.locator('[data-workspace-close]').click();
    await page.locator('.cap-expand-strip').click();
    await page.locator('[data-workspace-view=terminal]').click();
    await page.waitForFunction(() => document.querySelector('.xterm-screen')?.textContent.includes('__value:preserved__'));
    await page.reload();
    await page.locator('.cap-expand-strip').click();
    await page.locator('[data-workspace-view=terminal]').click();
    await page.waitForFunction(() => document.querySelectorAll('.terminal-tab').length === 2 &&
      document.querySelector('.xterm-screen')?.textContent.includes('__value:preserved__'));
    const after = await page.evaluate(async sid => (await Tomo.api('/api/sessions/' + sid + '/terminals')).terminals, sids[0]);
    assert.equal(after[0].pid, before[0].pid);
    await page.locator('[data-cap-max-toggle]').first().click();
    await page.screenshot({ path: path.join(artifacts, 'workspace-terminal-expanded.png') });
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 430, height: 850 });
    if (await page.locator('html').evaluate(el => el.classList.contains('is-rail-open'))) await page.locator('#navToggle').click();
    await page.waitForFunction(() => document.querySelector('.app-rail').getBoundingClientRect().right <= 1);
    await page.waitForFunction(() => document.querySelector('.terminal-stage')?.clientWidth > 0);
    await page.screenshot({ path: path.join(artifacts, 'workspace-terminal-mobile.png') });
    await page.evaluate(() => Tomo.applyTheme('light'));
    await page.waitForFunction(() => {
      const text = getComputedStyle(document.body).color;
      return getComputedStyle(document.querySelector('[data-terminal-create]')).color === text &&
        getComputedStyle(document.querySelector('.workspace-nav .active')).color === text;
    });
    await page.screenshot({ path: path.join(artifacts, 'workspace-terminal-light.png') });
    page.on('dialog', dialog => dialog.accept());
    await page.locator('.terminal-tab-close').last().click();
    await page.waitForFunction(() => document.querySelectorAll('.terminal-tab').length === 1);

    // Hold creation across a round trip through another chat, then finish it.
    await page.setViewportSize({ width: 1440, height: 950 });
    let releaseCreate, markArrived;
    const pendingCreate = new Promise(resolve => { releaseCreate = resolve; });
    const arrived = new Promise(resolve => { markArrived = resolve; });
    const createUrl = base + '/api/sessions/' + sids[0] + '/terminals';
    await page.route(createUrl, async route => {
      if (route.request().method() === 'POST') { markArrived(); await pendingCreate; }
      await route.continue();
    });
    try {
      await page.locator('[data-terminal-create]').first().click();
      await arrived;
      await page.locator('.session-item[data-id="' + sids[1] + '"]').click();
      await page.waitForFunction(sid => document.querySelector('.chat-wrap')?.dataset.sessionId === sid &&
        document.querySelector('[data-terminal-status]')?.textContent === 'Shell ready', sids[1]);
      await page.locator('.session-item[data-id="' + sids[0] + '"]').click();
      await page.waitForFunction(sid => document.querySelector('.chat-wrap')?.dataset.sessionId === sid &&
        document.querySelectorAll('.terminal-tab').length === 1 &&
        !document.querySelector('[data-terminal-create]')?.disabled, sids[0]);
      releaseCreate();
      await page.waitForFunction(() => document.querySelectorAll('.terminal-tab').length === 2 &&
        document.querySelector('[data-terminal-status]')?.textContent === 'Shell ready');
    } finally {
      releaseCreate();
      await page.unroute(createUrl);
    }
    assert.deepEqual(errors, []);
    console.log('PASS: multiple PTYs, chat isolation, shell preservation, files/preview, background artifacts, panel close, reload replay, timer, late creation, desktop/mobile/light layouts');
    console.log('Screenshots: ' + artifacts);
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(artifacts, 'terminal-ui-failure.png') }).catch(() => {});
    throw error;
  } finally {
    if (browser) await browser.close();
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      await new Promise(resolve => {
        const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
        child.kill('SIGTERM');
      });
    }
    // Preserve screenshots while discarding the temporary accounts and DB.
    if (path.resolve(artifacts) !== path.resolve(home)) fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

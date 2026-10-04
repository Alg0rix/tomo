/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/multi_chat.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  // Render the real page/composer, then exercise real JS over controllable HTTP/SSE.
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-multichat-'));
  const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from fastapi.testclient import TestClient
from app.main import app
from app.core.deps import require_auth
app.dependency_overrides[require_auth] = lambda: True
response = TestClient(app).get('/sessions')
assert response.status_code == 200, response.text
print(response.text)
`], { cwd: root, env: { ...process.env, TOMO_HOME: home }, maxBuffer: 2 * 1024 * 1024 }).toString().replaceAll('http://testserver', '');
  const agents = [{ id: 'main', name: 'Tomo', enabled: true }];
  const sessions = ['a', 'b'].map(id => ({ id, title: 'Chat ' + id.toUpperCase(), agent_id: 'main', agent_ids: ['main'], active_turn: false, message_count: 1, updated_at: 1 }));
  const histories = new Map(sessions.map(s => [s.id, [{ type: 'user', agent_id: 'main', content: 'History ' + s.id }, { type: 'final', agent_id: 'main', content: 'Ready' }]]));
  const subscribers = new Map();
  const sends = [], stops = [], listens = [], uploads = [], permissions = [];
  let releaseCreate, delayCreate = false;
  const event = (res, type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream');
      if (fs.existsSync(file)) res.end(fs.readFileSync(file)); else { res.statusCode = 404; res.end(); }
      return;
    }
    if (url.pathname === '/mobile-preview.html') {
      res.setHeader('Content-Type', 'text/html');
      res.end('<!doctype html><meta name="viewport" content="width=device-width"><style>body{margin:0;padding:24px;background:#faf9f6;color:#252525;font:16px/1.6 system-ui}h1{font-size:28px;line-height:1.2}p{color:#666}button{padding:12px;border:1px solid #ddd;border-radius:8px;background:white}</style><small>SESSION ARTIFACT</small><h1>Your mobile workspace</h1><p>Preview files here without leaving the conversation.</p><button>Preview action</button>');
      return;
    }
    if (url.pathname === '/sessions') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
    if (url.pathname === '/api/sessions' && req.method === 'GET') return json({ sessions, agents });
    if (url.pathname === '/api/sessions' && req.method === 'POST') {
      if (delayCreate) await new Promise(resolve => { releaseCreate = resolve; });
      const id = 'new-' + sessions.length;
      sessions.push({ id, title: 'New conversation', agent_id: 'main', agent_ids: ['main'], active_turn: false });
      histories.set(id, []);
      return json({ session_id: id });
    }
    const match = url.pathname.match(/^\/api\/sessions\/([^/]+)\/(.*)$/);
    if (match) {
      const [, id, action] = match;
      const session = sessions.find(s => s.id === id);
      if (action === 'chat/stream') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
        if (req.method === 'POST') {
          let raw = ''; for await (const chunk of req) raw += chunk;
          const body = JSON.parse(raw);
          sends.push({ id, message: body.message });
          if (body.attachment_ids?.length) {
            assert.deepEqual(body.attachment_ids, ['image-1']);
            assert.equal(session.mode, 'off', 'Draft permission must apply before the first agent turn');
          }
          session.active_turn = true;
          histories.get(id).push({ type: 'user', content: body.message, agent_id: 'main' });
          event(res, 'turn.start', { agent_id: 'main', turn_id: id });
          event(res, 'delta', { agent_id: 'main', content: 'Working in ' + id });
        } else {
          listens.push(id);
          event(res, 'state', { agent_id: 'main', busy: session.active_turn, resumed: session.active_turn });
          if (session.active_turn) event(res, 'turn.start', { agent_id: 'main', resumed: true });
          event(res, 'caught_up', {});
        }
        const set = subscribers.get(id) || new Set(); subscribers.set(id, set); set.add(res);
        res.on('close', () => set.delete(res));
        return;
      }
      if (action === 'chat') return json({ entries: histories.get(id) || [] });
      if (action === 'pending') return json({ active_turn: session.active_turn, approvals: [], clarifications: [] });
      if (action === 'approval-mode') {
        if (req.method === 'PUT') {
          let raw = ''; for await (const chunk of req) raw += chunk;
          session.mode = JSON.parse(raw).mode;
          permissions.push({ id, mode: session.mode });
        }
        return json({ mode: session.mode || 'smart' });
      }
      if (action === 'reasoning-effort') return json({ profile_id: 'test', model: 'Test model', model_profiles: [{ id: 'test', name: 'Test', models: ['Test model'] }], reasoning_efforts: ['low', 'high'], reasoning_effort: 'high' });
      if (action === 'artifacts') return json({ artifacts: [] });
      if (action === 'attachments' && req.method === 'POST') {
        for await (const chunk of req) {} // Consume the real multipart upload.
        uploads.push(id);
        return json({ id: 'image-1', original_name: 'image.png', size_bytes: 68 });
      }
      if (action === 'chat/stop') {
        stops.push(id); session.active_turn = false;
        histories.get(id).push({ type: 'error', content: 'Stopped', agent_id: 'main' });
        return json({ ok: true });
      }
    }
    if (url.pathname === '/api/workplaces') return json({ workplaces: [] });
    if (url.pathname === '/api/skills') return json({ skills: [] });
    if (url.pathname === '/api/mcp-servers') return json({ servers: [] });
    json({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/sessions?s=a`);
    const input = page.locator('.chat-input');
    await input.waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.chatInit === '1');
    await input.fill('Run A'); await input.press('Enter');
    await page.waitForFunction(() => document.querySelector('.composer').classList.contains('is-generating'));
    await page.locator('.session-group-running [data-id="a"]').waitFor();
    await page.waitForFunction(() => document.querySelector('.chat-scroll').textContent.includes('Working in a'));
    // Streaming must respect reading older output, until bottom/Follow is chosen.
    const emitDelta = content => {
      for (const res of subscribers.get('a') || []) event(res, 'delta', { agent_id: 'main', content });
    };
    emitDelta('\n\n' + 'Streaming line\n\n'.repeat(100));
    await page.waitForFunction(() => {
      const el = document.querySelector('.chat-scroll');
      return el.scrollHeight - el.scrollTop - el.clientHeight < 3 && el.scrollHeight > el.clientHeight * 2;
    });
    await page.locator('.chat-scroll').hover();
    await page.mouse.wheel(0, -350);
    await page.locator('.chat-follow').waitFor({ state: 'visible' });
    await page.waitForTimeout(250);
    const readingTop = await page.locator('.chat-scroll').evaluate(el => el.scrollTop);
    for (let n = 0; n < 5; n++) {
      emitDelta(`\n\nContinued ${n}\n\n`);
      await page.waitForTimeout(100);
    }
    for (const res of subscribers.get('a') || []) {
      event(res, 'tool', { tool: 'read_file', call_id: 'read-slow', args: { path: 'slow' }, agent_id: 'main' });
      event(res, 'tool', { tool: 'read_file', call_id: 'read-fast', args: { path: 'fast' }, agent_id: 'main' });
      event(res, 'tool_result', { tool: 'read_file', call_id: 'read-fast', result: 'Fast file output', agent_id: 'main' });
    }
    await page.waitForFunction(() => document.querySelector('[data-call-id="read-fast"]')?._res.textContent.includes('Fast file output'));
    assert.equal(await page.locator('[data-call-id="read-slow"]').evaluate(el => el.classList.contains('loading')), true);
    for (const res of subscribers.get('a') || []) event(res, 'tool_result', {
      tool: 'read_file', call_id: 'read-slow', result: 'Slow file output', agent_id: 'main',
    });
    await page.waitForFunction(() => document.querySelector('[data-call-id="read-slow"]')?._res.textContent.includes('Slow file output'));
    await page.waitForTimeout(200);
    assert.ok(Math.abs(await page.locator('.chat-scroll').evaluate(el => el.scrollTop) - readingTop) < 3, 'Streaming or parallel tool results pulled the reader down');
    await page.screenshot({ path: path.join(os.tmpdir(), 'tomo-stream-follow.png') });
    await page.locator('.chat-follow').click();
    await page.waitForFunction(() => !document.querySelector('.chat-follow').offsetParent);
    await page.locator('.chat-scroll').hover();
    await page.mouse.wheel(0, -350);
    await page.locator('.chat-follow').waitFor({ state: 'visible' });
    await page.mouse.wheel(0, 100000);
    await page.locator('.chat-follow').waitFor({ state: 'hidden' });
    emitDelta('\n\nFollowing again\n\n');
    await page.waitForFunction(() => {
      const el = document.querySelector('.chat-scroll');
      return el.scrollHeight - el.scrollTop - el.clientHeight < 3;
    });
    const imageFile = {
      name: 'image.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT4sAAAAASUVORK5CYII=', 'base64'),
    };
    await page.locator('#newChatBtn').click();
    await page.locator('#newChatConfirm').click();
    await page.locator('.composer-mode').click();
    await page.locator('.attachment-input').setInputFiles(imageFile);
    await page.locator('.composer-mobile-more-btn').click();
    await page.locator('.chat-files-btn').click();
    await page.waitForFunction(() => document.querySelector('.composer-mode-key').textContent === 'Auto', null, { timeout: 3000 });
    await page.locator('.attachment-preview .attachment-chip').waitFor();
    assert.match(await page.locator('.chat-agent-panel').textContent(), /No files yet/);
    await page.locator('.chat-agent-panel .cap-collapse').click();
    assert.equal(sessions.length, 2, 'Unsent settings and attachments must not create a session');
    assert.deepEqual(uploads, [], 'Images stay local until Send');
    assert.deepEqual(permissions, []);
    assert.equal(sends.length, 1, 'No dummy message');
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'new-chat-draft.png') });
    // Abandon the draft, then confirm neither files nor permissions leak to the next one.
    await page.locator('#newChatBtn').click(); await page.locator('#newChatConfirm').click();
    assert.equal(await page.locator('.composer-mode-key').textContent(), 'Smart');
    assert.equal(await page.locator('.attachment-preview .attachment-chip').count(), 0);
    assert.equal(sessions.length, 2, 'Cancelled New chat leaves no session in history');
    assert.equal(await input.inputValue(), '');
    assert.equal(await page.locator('.composer.is-generating').count(), 0, 'Draft must not inherit running/Stop state');
    await page.locator('.composer-mode').click();
    await page.locator('.attachment-input').setInputFiles(imageFile);
    delayCreate = true;
    await input.fill('Run new'); await input.press('Enter');
    while (!releaseCreate) await new Promise(resolve => setTimeout(resolve, 10));
    releaseCreate(); delayCreate = false; releaseCreate = null;
    await page.waitForFunction(() => (document.querySelector('#sessionChat').dataset.sessionId || '').startsWith('new-'));
    await page.locator('.session-group-running [data-id^="new-"]').waitFor();
    assert.deepEqual(sends, [{ id: 'a', message: 'Run A' }, { id: 'new-2', message: 'Run new' }], 'Enter sends once, only to the new chat');
    assert.equal(sessions.filter(s => s.active_turn).length, 2);
    assert.equal(sessions.length, 3, 'First Send creates only one session');
    assert.deepEqual(permissions, [{ id: 'new-2', mode: 'off' }]);
    assert.deepEqual(uploads, ['new-2']);
    await page.waitForFunction(() => document.querySelector('.composer-reasoning-model').textContent === 'Test model');
    assert.doesNotMatch(await page.locator('#sessionList').textContent(), /\bmsgs\b/, 'Sidebar omits message counts');
    assert.doesNotMatch(await page.locator('.chat-scroll').textContent(), /Run A|Working in a/);
    await input.fill('Unsent new draft');
    await page.locator('#sessionList [data-id="a"]').click();
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.sessionId === 'a' && document.querySelector('#sessionChat').dataset.chatInit === '1');
    await page.locator('.composer.is-generating').waitFor();
    assert.ok(listens.includes('a'), 'Returning to running chat reattaches SSE');
    await page.locator('.chat-stop').click();
    await page.waitForFunction(() => !document.querySelector('.composer').classList.contains('is-generating'));
    assert.deepEqual(stops, ['a'], 'Stop affects only the selected chat');
    assert.equal(sessions.find(s => s.id === 'new-2').active_turn, true);
    await page.locator('#sessionList [data-id="new-2"]').click();
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.sessionId === 'new-2' && document.querySelector('#sessionChat').dataset.chatInit === '1');
    assert.equal(await input.inputValue(), 'Unsent new draft', 'Switching preserves unsent text');
    // Complete an offscreen turn; polling must remove Running without touching this view.
    await page.locator('#sessionList [data-id="b"]').click();
    const running = sessions.find(s => s.id === 'new-2'); running.active_turn = false;
    histories.get('new-2').push({ type: 'final', content: 'New finished', agent_id: 'main' });
    for (const res of subscribers.get('new-2') || []) { event(res, 'turn.end', { session_id: 'new-2' }); res.end(); }
    await page.waitForFunction(() => !document.querySelector('.session-group-running'), { timeout: 8000 });
    assert.doesNotMatch(await page.locator('.chat-scroll').textContent(), /New finished/);
    // Create resolving after a switch must still send to its own session, never steal B.
    delayCreate = true;
    await page.locator('#newChatBtn').click(); await page.locator('#newChatConfirm').click();
    await input.fill('Late create'); await input.press('Enter');
    while (!releaseCreate) await new Promise(resolve => setTimeout(resolve, 10));
    await page.locator('#sessionList [data-id="b"]').click();
    releaseCreate();
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.sessionId === 'b' && document.querySelector('#sessionChat').dataset.chatInit === '1');
    await page.waitForTimeout(200);
    assert.deepEqual(sends.at(-1), { id: 'new-3', message: 'Late create' });
    assert.doesNotMatch(await page.locator('.chat-scroll').textContent(), /Late create|Working in new-3/);
    await page.locator('#sessionList [data-id="a"]').click();
    await page.locator('#sessionList [data-id="b"]').click();
    await page.waitForFunction(() => document.querySelector('#sessionChat').dataset.sessionId === 'b' && document.querySelector('#sessionChat').dataset.chatInit === '1');
    await input.fill('Only B'); await page.locator('.chat-send').click();
    await page.waitForTimeout(100);
    assert.equal(sends.filter(s => s.message === 'Only B').length, 1, 'Repeated switching never stacks click handlers');
    assert.equal(sends.at(-1).id, 'b');
    assert.deepEqual(errors, []);
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'multi-chat.png') });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.session-running-dot').first().evaluate(el => getComputedStyle(el).animationName), 'none');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#railMobileOpen').click();
    await page.locator('#sessionList [data-id="b"]').click();
    assert.equal(await page.locator('html.is-rail-open').count(), 0, 'Mobile switching closes the drawer');
    await page.waitForTimeout(300);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true });
    const swipe = async (x1, y1, x2, y2, cancel = false) => {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x1, y: y1 }] });
      for (let i = 1; i <= 8; i++) {
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x1 + (x2 - x1) * i / 8, y: y1 + (y2 - y1) * i / 8 }] });
        await page.waitForTimeout(16);
      }
      await cdp.send('Input.dispatchTouchEvent', { type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: [] });
      await page.waitForTimeout(250);
    };
    await swipe(18, 220, 18, 110);
    assert.equal(await page.locator('html.is-rail-open').count(), 0, 'Vertical scroll must not open a drawer');
    await swipe(18, 180, 200, 180, true);
    assert.equal(await page.locator('html.is-rail-open').count(), 0, 'Cancelled opening restores the closed drawer');
    await swipe(18, 180, 200, 180);
    assert.equal(await page.locator('html.is-rail-open').count(), 1, 'Left-edge swipe opens sidebar');
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'mobile-sidebar.png') });
    await swipe(245, 230, 24, 230);
    assert.equal(await page.locator('html.is-rail-open').count(), 0, 'Reverse swipe closes sidebar');
    await swipe(375, 220, 100, 220);
    assert.equal(await page.locator('.chat-agent-panel').getAttribute('data-cap-open'), '1', 'Right-edge swipe opens workspace');
    await page.evaluate(() => TomoArtifacts.openPreview({ url: '/mobile-preview.html', filename: 'mobile-preview.html', title: 'Mobile workspace preview', session_id: 'b' }));
    await page.locator('.ap-html-frame').waitFor({ state: 'visible' });
    await swipe(100, 85, 200, 85, true);
    assert.equal(await page.locator('.ap-html-frame').count(), 1, 'Cancelling a close keeps the current artifact preview');
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'mobile-workspace.png') });
    await swipe(100, 85, 375, 85);
    assert.equal(await page.locator('.chat-agent-panel').getAttribute('data-cap-open'), '0', 'Reverse swipe closes workspace');
    await page.locator('.composer-mobile-more-btn').click();
    await page.locator('.composer-mobile-more-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.composer-mobile-more-panel').getAttribute('aria-modal'), 'true');
    assert.equal(await page.locator('.composer-actions .attach-btn').count(), 1);
    assert.equal(await page.locator('.composer-actions .composer-mode').count(), 1);
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'mobile-chat-options.png') });
    await page.keyboard.press('Escape');
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.waitForTimeout(100);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal overflow at ' + width);
      const bar = await page.locator('.app-mobile-bar').boundingBox();
      const scroll = await page.locator('.chat-scroll').boundingBox();
      assert.equal(scroll.y, bar.y, 'Chat scrolls underneath the floating controls');
      await page.locator('.chat-scroll').evaluate(el => { el.scrollTop = 0; });
      const firstMessage = await page.locator('.chat-scroll .msg').first().boundingBox();
      assert.ok(firstMessage.y >= bar.y + bar.height, 'Initial message clears the controls');
      const composer = await page.locator('.composer-shell').boundingBox();
      assert.ok(composer.height <= 120, 'Compact composer at ' + width + ': ' + JSON.stringify(await page.locator('.composer-shell').evaluate(el => Array.from(el.children, child => ({ class: child.className, height: child.getBoundingClientRect().height })))));
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#mobileNewChat').click();
    await page.locator('#newChatModal').waitFor({ state: 'visible' });
    await page.locator('#newChatModal .modal-foot [data-close]').click();
    assert.equal(sessions.length, 4, 'Mobile New chat uses the existing draft flow, not an immediate session creation');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    assert.equal(await page.locator('.app-mobile-bar').evaluate(el => getComputedStyle(el, '::before').transitionDuration), '0.16s');
    for (const res of subscribers.get('b') || []) event(res, 'delta', { agent_id: 'main', content: '\n\n' + 'Files stay with this conversation. Open the workspace to preview an artifact, then return to the same chat without losing your place.\n\n'.repeat(8) });
    await page.waitForFunction(() => document.querySelector('.chat-scroll').scrollHeight > document.querySelector('.chat-scroll').clientHeight + 80);
    await page.locator('.chat-scroll').evaluate(el => { el.scrollTop = 0; });
    await page.waitForFunction(() => document.querySelector('.app-mobile-bar').style.getPropertyValue('--mobile-chrome-opacity') === '0.000');
    await page.waitForTimeout(180);
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'multi-chat-mobile.png') });
    await page.locator('.chat-scroll').evaluate(el => { el.scrollTop = 40; });
    await page.waitForFunction(() => document.querySelector('.app-mobile-bar').style.getPropertyValue('--mobile-chrome-opacity') === '1.000');
    assert.match(await page.locator('.app-mobile-bar').evaluate(el => getComputedStyle(el, '::before').backdropFilter), /blur/);
    await page.waitForTimeout(180);
    if (process.env.BB_THREAD_STORAGE) await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE, 'mobile-scroll-overlay.png') });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.waitForFunction(() => document.querySelector('.composer-toolbar-left .attach-btn'));
    assert.equal(await page.locator('.composer-toolbar-left .attach-btn').count(), 1, 'Desktop restores existing toolbar');
    assert.equal(await page.locator('.composer-toolbar-left .composer-mode').count(), 1);
    assert.deepEqual(errors, []);
    console.log('Multi-chat passed: isolated chats, resume, mobile drawer swipes, cancellation, sheet, compact widths and desktop restoration');
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

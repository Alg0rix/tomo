/* NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/chat_lazy_history.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-lazy-chat-'));
  const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from fastapi.testclient import TestClient
from app.main import app
from app.core.deps import require_auth
app.dependency_overrides[require_auth] = lambda: True
print(TestClient(app).get('/sessions').text)
`], { cwd: root, env: { ...process.env, TOMO_HOME: home }, maxBuffer: 2 * 1024 * 1024 }).toString().replaceAll('http://testserver', '');
  const entries = Array.from({ length: 65 }, (_, n) => [
    { message_id: n * 2 + 1, type: 'user', content: `Question ${n}`, agent_id: 'main' },
    { message_id: n * 2 + 2, type: 'final', content: (`Answer ${n} with enough text for scrolling. `).repeat(20), agent_id: 'main' },
  ]).flat();
  const requests = [];
  let delayOlder = false, releaseOlder;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/css');
      return res.end(fs.existsSync(file) ? fs.readFileSync(file) : '');
    }
    if (url.pathname === '/sessions') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/api/sessions') return json({
      sessions: ['a', 'b'].map(id => ({ id, title: id, agent_id: 'main', agent_ids: ['main'], active_turn: false })),
      agents: [{ id: 'main', name: 'Tomo', enabled: true }],
    });
    if (url.pathname === '/api/sessions/a/chat/queries') return json({ queries: entries.filter(e => e.type === 'user') });
    if (url.pathname === '/api/sessions/a/chat') {
      requests.push(url.search);
      if (delayOlder && url.searchParams.has('before')) await new Promise(resolve => { releaseOlder = resolve; });
      let page = entries.filter(e => !url.searchParams.has('before') || e.message_id < Number(url.searchParams.get('before')));
      if (url.searchParams.has('since')) page = page.filter(e => e.message_id >= Number(url.searchParams.get('since')));
      else page = page.slice(-40);
      return json({ entries: page, has_more: page[0]?.message_id > 1, before: page[0]?.message_id });
    }
    if (url.pathname === '/api/sessions/b/chat/queries') return json({ queries: [{ message_id: 201, content: 'Other chat' }] });
    if (url.pathname === '/api/sessions/b/chat') return json({ entries: [{ message_id: 201, type: 'user', content: 'Other chat' }, { message_id: 202, type: 'final', content: 'Ready' }] });
    if (url.pathname.endsWith('/pending')) return json({ active_turn: false, approvals: [], clarifications: [] });
    return json({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/sessions?s=a`);
    await page.waitForFunction(() => document.querySelectorAll('.chat-query-item').length === 65);
    await page.waitForFunction(() => document.querySelectorAll('.chat-scroll .turn').length === 20);
    assert.deepEqual(requests, ['?limit=20']);
    assert.equal(await page.locator('button.chat-load-older').count(), 0);
    const anchor = await page.evaluate(() => {
      const scroll = document.querySelector('.chat-scroll');
      scroll._tomoStickCleanup?.();
      scroll.scrollTop = 80;
      const turn = Array.from(scroll.querySelectorAll('.turn')).find(el => el.getBoundingClientRect().bottom > scroll.getBoundingClientRect().top);
      return { id: turn.dataset.queryId, top: turn.getBoundingClientRect().top };
    });
    await page.waitForFunction(() => document.querySelectorAll('.chat-scroll .turn').length === 40);
    const anchorTop = await page.locator(`[data-query-id="${anchor.id}"].turn`).evaluate(el => el.getBoundingClientRect().top);
    assert.ok(Math.abs(anchor.top - anchorTop) < 3, `Scroll anchor moved: ${anchor.top} -> ${anchorTop}`);
    assert.equal(await page.locator('.chat-query-item').count(), 65);
    await page.locator('.chat-query-item').first().click();
    await page.waitForFunction(() => {
      const turn = document.querySelector('.turn[data-query-id="chat-message-1"]');
      const scroll = document.querySelector('.chat-scroll');
      return turn && turn.getBoundingClientRect().top >= scroll.getBoundingClientRect().top && turn.getBoundingClientRect().top < scroll.getBoundingClientRect().bottom;
    });
    assert.equal(await page.locator('.chat-query-item').count(), 65);
    assert.equal(await page.locator('.chat-scroll .turn').count(), 65);
    // Return to a fresh window, then switch sessions while an older page is in flight.
    await page.goto(`http://127.0.0.1:${server.address().port}/sessions?s=a`);
    await page.waitForFunction(() => document.querySelectorAll('.chat-scroll .turn').length === 20);
    delayOlder = true;
    await page.evaluate(() => { const el = document.querySelector('.chat-scroll'); el._tomoStickCleanup?.(); el.scrollTop = 0; });
    await page.waitForFunction(() => document.querySelector('.chat-load-older')?.textContent.includes('Loading'));
    await page.locator('.session-item[data-id="b"]').click();
    await page.waitForFunction(() => document.querySelector('#sessionChat')?.dataset.sessionId === 'b' && document.querySelector('.chat-scroll')?.textContent.includes('Other chat'));
    releaseOlder();
    await page.waitForTimeout(200);
    assert.equal(await page.locator('.chat-scroll .turn').count(), 1);
    assert.equal(await page.locator('.chat-query-item').count(), 1);
    assert.deepEqual(errors, []);
    console.log('Lazy history passed: recent window, full rail, automatic scroll loading, stable anchor, rail jump, session race');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/model_picker.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-model-picker-'));
const pages = JSON.parse(execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
import json
from fastapi.testclient import TestClient
from app.main import app
from app.core.deps import require_auth
app.dependency_overrides[require_auth] = lambda: True
client = TestClient(app)
print(json.dumps({p: client.get(p).text.replace('http://testserver', '') for p in ['/system', '/sessions']}))
`], { cwd: root, env: { ...process.env, TOMO_HOME: home }, maxBuffer: 4 * 1024 * 1024 }).toString());

(async () => {
  let profiles = [], sessions = [], settings = {}, chosen = '', effort = '', failSave = false;
  const models = ['kimi-k2.5', 'gpt-5.4', 'minimax-m2.7'];
  const agents = [{ id: 'main', name: 'Tomo', enabled: true }];
  function state() {
    const p = profiles[0];
    const model = chosen || p?.model || '';
    const efforts = model.startsWith('gpt-') ? ['low', 'medium', 'high'] : [];
    return { profile_id: p?.id, profile_name: p?.name, model, model_profiles: profiles.map(p => ({ id: p.id, name: p.name, models: p.available_models })), reasoning_efforts: efforts, default_reasoning_effort: efforts.at(-1), reasoning_effort: effort || efforts.at(-1), selected_reasoning_effort: effort || null };
  }
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream');
      if (fs.existsSync(file)) res.end(fs.readFileSync(file)); else { res.statusCode = 404; res.end(); }
      return;
    }
    if (pages[url.pathname]) { res.setHeader('Content-Type', 'text/html'); res.end(pages[url.pathname]); return; }
    if (url.pathname === '/api/llm-profiles/provider-models') return json({ models });
    if (url.pathname === '/api/llm-profiles/chat-options') return json(state());
    if (url.pathname === '/api/llm-profiles') {
      if (req.method === 'POST') {
        assert.equal(body.base_url, 'https://opencode.ai/zen/go/v1');
        assert.equal(body.api_key, 'test-token');
        const p = { ...body, id: 'go', available_models: models, api_key: '••••oken', api_key_set: true }; profiles = [p]; return json(p);
      }
      return json({ profiles, default_id: 'go' });
    }
    if (url.pathname === '/api/settings') { settings = body; return json(settings); }
    if (url.pathname === '/api/sessions') {
      if (req.method === 'POST') { sessions.push({ id: 'created', agent_id: 'main', coordinator_id: 'main', agent_ids: ['main'], title: 'New chat', message_count: 0 }); return json({ session_id: 'created' }); }
      return json({ sessions, agents });
    }
    if (url.pathname.endsWith('/reasoning-effort')) {
      if (req.method === 'PUT') {
        if (failSave) { res.statusCode = 400; return json({ detail: 'Provider rejected effort' }); }
        effort = body.reasoning_effort;
      }
      const snapshot = state();
      if (req.method === 'GET') await new Promise(resolve => setTimeout(resolve, 100));
      return json(snapshot);
    }
    if (url.pathname.endsWith('/model')) { chosen = body.model || ''; effort = ''; return json(state()); }
    if (url.pathname.endsWith('/chat/stream')) { res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.end('event: state\ndata: {"busy":false}\n\n'); return; }
    if (url.pathname.endsWith('/chat')) return json({ entries: [] });
    if (url.pathname.endsWith('/approval-mode')) return json({ mode: 'smart' });
    if (url.pathname.endsWith('/pending')) return json({ active_turn: false, approvals: [], clarifications: [] });
    if (url.pathname === '/api/workplaces') return json({ workplaces: [] });
    if (url.pathname === '/api/skills') return json({ skills: [] });
    if (url.pathname === '/api/mcp-servers') return json({ servers: [] });
    json({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) {
      profiles = []; sessions = []; chosen = ''; effort = ''; failSave = false;
      const page = await browser.newPage({ viewport: { width, height: 850 }, isMobile: width < 500, hasTouch: width < 500 });
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      const base = `http://127.0.0.1:${server.address().port}`;
      await page.goto(base + '/system#models');
      await page.locator('#addProfileBtn').click();
      await page.locator('#profProvider').selectOption('opencode-go');
      await page.locator('#profApiKey').fill('test-token');
      await page.locator('#profApiKey').press('Tab');
      await page.waitForFunction(() => document.querySelector('#profModelSelect').options.length === 4);
      await page.locator('#profSave').click();
      await page.waitForFunction(() => document.querySelector('#profileFormCard').classList.contains('hidden'));
      await page.locator('#systemNav a[data-section="general"]').click();
      assert.equal(await page.locator('#aux-session_title option').count(), 4);
      assert.equal(await page.locator('#aux-memory_extraction').inputValue(), '');
      await page.locator('#saveGeneral').click();
      await page.waitForTimeout(100);
      assert.equal(settings.memory_extraction_profile_id, '');
      assert.equal(settings.session_title_profile_id, '');
      await page.goto(base + '/sessions?agent=main');
      const trigger = page.locator('.composer-reasoning-trigger');
      await trigger.waitFor({ state: 'visible' });
      assert.equal(sessions.length, 0, 'Picker is available before the first message');
      await trigger.click();
      const picker = page.locator('.composer-reasoning-popover');
      await picker.waitFor({ state: 'visible' });
      assert.equal(await page.locator('.composer-effort-select').isDisabled(), true);
      await page.locator('.composer-model-search').fill('gpt');
      assert.equal(await page.locator('.composer-model-select option').count(), 2, 'Search keeps the current model');
      await page.locator('.composer-model-select').selectOption(JSON.stringify({ profile_id: 'go', model: 'gpt-5.4' }));
      try { await page.waitForFunction(() => !document.querySelector('.composer-effort-select').disabled, null, { timeout: 5000 }); }
      catch (e) { console.error({ chosen, sessions, errors, status: await page.locator('.composer-picker-status').textContent() }); throw e; }
      assert.equal(sessions.length, 1, 'First selection creates a chat once');
      await page.waitForTimeout(200);
      assert.equal(await page.locator('.composer-reasoning-model').textContent(), 'gpt-5.4', 'Late reads cannot overwrite a saved selection');
      await page.locator('.composer-effort-select').selectOption('low');
      await page.waitForFunction(() => document.querySelector('.composer-reasoning-trigger-effort').textContent === 'low');
      failSave = true;
      await page.locator('.composer-effort-select').selectOption('medium');
      await page.waitForFunction(() => document.querySelector('.composer-picker-status').textContent.includes('rejected'));
      assert.equal(await page.locator('.composer-effort-select').inputValue(), 'low');
      failSave = false;
      await page.locator('.composer-effort-select').selectOption('high');
      await page.waitForFunction(() => document.querySelector('.composer-reasoning-trigger-effort').textContent === 'high');
      const rect = await picker.boundingBox();
      assert(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= width + 1 && rect.y + rect.height <= 851, 'Picker stays inside viewport');
      if (process.env.MODEL_PICKER_SCREENSHOTS) {
        fs.mkdirSync(process.env.MODEL_PICKER_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.MODEL_PICKER_SCREENSHOTS, `model-picker-${width}.png`) });
      }
      await page.keyboard.press('Escape');
      assert.equal(await picker.isVisible(), false);
      await trigger.click();
      await page.locator('.composer-reasoning-reset').click();
      await page.waitForFunction(() => document.querySelector('.composer-reasoning-model').textContent === 'kimi-k2.5');
      await page.locator('.composer-picker-close').click();
      assert.deepEqual(errors, []);
      console.log(`${width}px: provider token/catalog, auxiliary defaults, new-chat picker, model/effort saves, failure recovery, viewport and reset passed`);
      await page.close();
    }
  } finally { await browser.close(); server.close(); server.closeAllConnections(); fs.rmSync(home, { recursive: true, force: true }); }
})().catch(e => { console.error(e); process.exitCode = 1; });

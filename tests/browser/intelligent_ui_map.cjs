/* NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-intelligent-ui-'));
  const node = require('./fixtures/coastal-itinerary.cjs');
  // Go through the real tool validator before sending saved history to the page.
  const validate = (uiId, tree) => JSON.parse(execFileSync(path.join(root, '.venv/bin/python'), ['-c',
    'import json,sys; from app.runtime.tools.render_ui import run; print(run(json.load(sys.stdin)))'
  ], { cwd: root, input: JSON.stringify({ ui_id: uiId, tree }) }).toString());
  const spec = validate('coastal-trip', node);
  const bandungSpec = validate('bandung-food', require('./fixtures/bandung-food.cjs'));
  const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from fastapi.testclient import TestClient
from app.main import app
from app.services import store
store.create_user({'username': 'ui_preview', 'password': 'preview_password1'})
client = TestClient(app)
client.post('/login', data={'username': 'ui_preview', 'password': 'preview_password1'})
response = client.get('/sessions')
assert response.status_code == 200 and 'sessionsPage' in response.text
print(response.text)
`], { cwd: root, env: { ...process.env, TOMO_HOME: home }, maxBuffer: 3 * 1024 * 1024 }).toString().replaceAll('http://testserver', '');
  const entries = [
    { message_id: 1, type: 'user', content: 'Show a four-day California coastal itinerary with a real map, photos, and playable stops.', agent_id: 'main' },
    { message_id: 2, type: 'final', content: 'Explore the stops on the map, pause the sequence, or select a destination.', agent_id: 'main' },
    { message_id: 3, type: 'ui', params: spec, agent_id: 'main' },
    { message_id: 4, type: 'user', content: 'Show food near Bandung on a map.', agent_id: 'main' },
    { message_id: 5, type: 'final', content: 'Here are a few places to eat in Bandung.', agent_id: 'main' },
    { message_id: 6, type: 'ui', params: bandungSpec, agent_id: 'main' },
  ];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.png') ? 'image/png' : 'text/css');
      return res.end(fs.existsSync(file) ? fs.readFileSync(file) : '');
    }
    if (url.pathname === '/sessions') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/api/sessions') return json({ sessions: [{ id: 'intelligent-ui', title: 'California coast', agent_id: 'main', agent_ids: ['main'], active_turn: false }], agents: [{ id: 'main', name: 'Tomo', enabled: true }] });
    if (url.pathname.endsWith('/chat/queries')) return json({ queries: [entries[0]] });
    if (url.pathname.endsWith('/chat')) return json({ entries, has_more: false });
    if (url.pathname.endsWith('/pending')) return json({ active_turn: false, approvals: [], clarifications: [] });
    return json({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, reducedMotion: 'no-preference' });
    const errors = [];
    page.on('requestfailed', request => console.log('Network failure:', request.url(), request.failure().errorText));
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/sessions?s=intelligent-ui`);
    const widget = page.frameLocator('.gen-ui-sandbox iframe').first();
    await widget.locator('.leaflet-container').waitFor({ timeout: 30000 });
    const frame = page.frames().find(frame => frame.url() === 'about:srcdoc');
    const bandung = page.frameLocator('.gen-ui-sandbox iframe').nth(1);
    await bandung.locator('#bandung-status', { hasText: /Map loaded|unavailable/ }).waitFor({ timeout: 30000 });
    assert.equal(await bandung.locator('#bandung-status').textContent(), 'Map loaded');
    assert.match(await bandung.locator('.leaflet-control-attribution').innerText(), /OpenStreetMap contributors © CARTO/);
    assert.equal(await bandung.locator('path.leaflet-interactive').count(), 3);
    const bandungFrame = page.frames().filter(f => f.url() === 'about:srcdoc')[1];
    await bandungFrame.waitForFunction(() => Array.from(document.querySelectorAll('.leaflet-tile')).some(img => img.complete && img.naturalWidth > 0), null, { timeout: 30000 });
    await frame.waitForFunction(() => Number(document.getElementById('map-status').dataset.loaded) >= 2, { timeout: 30000 });
    await frame.waitForFunction(() => Array.from(document.querySelectorAll('.stop img')).every(img => img.complete && img.naturalWidth > 0), { timeout: 30000 });
    assert.equal(await widget.locator('.pin-dot').count(), 5);
    assert.equal(await widget.locator('.credit').count(), 5);
    assert.match(await widget.locator('.leaflet-control-attribution').innerText(), /USGS The National Map/);
    await widget.locator('#replay').click();
    await frame.waitForFunction(() => document.getElementById('map').dataset.state === 'playing');
    await widget.locator('#pause').click();
    await frame.waitForFunction(() => document.getElementById('map').dataset.state === 'paused');
    await widget.locator('[data-index="2"]').click();
    assert.match(await widget.locator('#current').textContent(), /Stop 3 of 5 · Big Sur/);
    assert.equal(await widget.locator('.stop.active').getAttribute('data-stop'), '2');
    const initialZoom = await frame.evaluate(() => tripMap.getZoom());
    await widget.locator('.leaflet-control-zoom-in').click();
    await frame.waitForFunction(zoom => window.tripMap.getZoom() > zoom, initialZoom);
    await frame.evaluate(() => { tripMap.fitBounds([[37.8078, -122.475], [34.1341, -118.3215]], { padding: [28, 28], animate: false }); tripController.seek(2); });
    await frame.waitForFunction(() => Array.from(document.querySelectorAll('.leaflet-tile')).some(img => img.complete && img.naturalWidth > 0));
    assert.equal(await page.locator('.gen-ui-block-hd').count(), 0);
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-coastal-map-v1.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await frame.waitForFunction(() => window.tripMap.getSize().x < 400);
    await widget.locator('[data-index="4"]').click();
    assert.match(await widget.locator('#current').textContent(), /Los Angeles/);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-coastal-map-mobile-v1.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS: Bandung CARTO tiles via import("leaflet"), live USGS tiles, sourced photos, five map pins, attribution, pause, replay, stop selection, zoom, mobile layout');
  } finally {
    await browser.close(); server.close(); fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

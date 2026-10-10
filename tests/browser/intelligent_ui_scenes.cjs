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
  const node = require('./fixtures/aircraft-rotation.cjs');
  // Go through the real tool validator before sending saved history to the page.
  const spec = JSON.parse(execFileSync(path.join(root, '.venv/bin/python'), ['-c',
    'import json,sys; from app.runtime.tools.render_ui import run; print(run(json.load(sys.stdin)))'
  ], { cwd: root, input: JSON.stringify({ ui_id: 'aircraft', tree: node }) }).toString());
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
  let entries = [
    { message_id: 1, type: 'user', content: 'Explain pitch, roll, and yaw with a 3D airplane I can rotate.', agent_id: 'main' },
    { message_id: 2, type: 'final', content: 'Explore the three aircraft rotation axes with sliders and demonstrations.', agent_id: 'main' },
    { message_id: 3, type: 'ui', params: spec, agent_id: 'main' },
  ];
  const tableSpec = { ui_id: 'plan-table', tree: { type: 'stack', title: 'Compare plans', description: 'Illustrative prices, not live offers.', children: [
    { type: 'table', columns: ['Plan', 'Monthly price', 'Seats', 'Cost per seat'], rows: [['Starter', '$12', '2', '$6'], ['Pro', '$25', '5', '$5'], ['Team', '$40', '10', '$4']] },
    { type: 'button', label: 'Show cost per seat as a chart', action: 'show_cost_per_seat' },
  ] } };
  const chartSpec = { ui_id: 'plan-chart', tree: { type: 'stack', title: 'Cost per seat', description: 'Same illustrative monthly prices divided by seats, in USD.', children: [
    { type: 'chart', data: [{ label: 'Starter', value: 6 }, { label: 'Pro', value: 5 }, { label: 'Team', value: 4 }] },
  ] } };
  const actions = [];
  let chartRequested = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.png') ? 'image/png' : 'text/css');
      return res.end(fs.existsSync(file) ? fs.readFileSync(file) : '');
    }
    if (url.pathname === '/sessions') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/api/sessions') return json({ sessions: [{ id: 'intelligent-ui', title: 'Aircraft rotation', agent_id: 'main', agent_ids: ['main'], active_turn: false }], agents: [{ id: 'main', name: 'Tomo', enabled: true }] });
    if (url.pathname.endsWith('/ui-actions')) {
      let body = ''; req.on('data', chunk => body += chunk);
      req.on('end', () => { actions.push(JSON.parse(body)); chartRequested = true; return json({ ok: true, accepted: true, mode: 'started' }); });
      return;
    }
    if (url.pathname.endsWith('/chat/stream')) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      if (chartRequested) {
        res.write('event: turn.start\ndata: ' + JSON.stringify({ turn_id: 'chart-followup', agent_id: 'main', agent: 'Tomo' }) + '\n\n');
        res.write('event: ui\ndata: ' + JSON.stringify({ ...chartSpec, mode: 'replace', agent_id: 'main', agent: 'Tomo', turn_id: 'chart-followup' }) + '\n\n');
        entries.push({ message_id: 4, type: 'user', content: '[UI action]\n' + JSON.stringify(actions[0]), agent_id: 'main' }, { message_id: 5, type: 'ui', params: chartSpec, agent_id: 'main' });
        chartRequested = false;
      }
      res.end('event: done\ndata: ' + JSON.stringify({ agent_id: 'main', agent: 'Tomo', turn_id: 'chart-followup', content: '' }) + '\n\nevent: caught_up\ndata: {}\n\nevent: turn.end\ndata: {}\n\n');
      return;
    }
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
    const widget = page.frameLocator('.gen-ui-sandbox iframe');
    await widget.locator('#status[data-ready="true"]').waitFor({ timeout: 30000 });
    const frame = page.frames().find(frame => frame.url() === 'about:srcdoc');
    assert.equal(await widget.locator('canvas').count(), 1);
    for (const [axis, value, component] of [['pitch', 25, 'z'], ['roll', -30, 'x'], ['yaw', 40, 'y']]) {
      await widget.locator('#' + axis).evaluate((el, value) => { el.value = value; el.dispatchEvent(new Event('input', { bubbles: true })); }, value);
      assert.equal(await widget.locator('#' + axis + '-value').textContent(), value + '°');
      const radians = await frame.evaluate(component => aircraft.plane.rotation[component], component);
      assert.ok(Math.abs(radians - value * Math.PI / 180) < 1e-6);
    }
    await widget.locator('#reset').click();
    assert.deepEqual(await frame.evaluate(() => aircraft.values), { pitch: 0, roll: 0, yaw: 0 });
    const cameraBefore = await frame.evaluate(() => aircraft.camera.position.toArray());
    await widget.locator('[data-demo="pitch"]').click();
    await frame.waitForFunction(() => aircraft.values.pitch === 35);
    assert.deepEqual(await frame.evaluate(() => aircraft.camera.position.toArray()), cameraBefore);
    const canvas = await widget.locator('canvas').boundingBox();
    await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
    await page.mouse.down(); await page.mouse.move(canvas.x + canvas.width / 2 + 90, canvas.y + canvas.height / 2 + 30, { steps: 10 }); await page.mouse.up();
    assert.notDeepEqual(await frame.evaluate(() => aircraft.camera.position.toArray()), cameraBefore);
    await widget.locator('#reset').click();
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-aircraft-v1.png'), fullPage: true });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await widget.locator('[data-demo="roll"]').click();
    assert.equal(await widget.locator('#roll-value').textContent(), '35°');
    await page.setViewportSize({ width: 390, height: 844 });
    await frame.waitForFunction(() => aircraft.renderer.domElement.clientWidth < 400);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-aircraft-mobile-v1.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1200 });
    entries = [{ message_id: 1, type: 'user', content: 'Compare these sample subscription plans.', agent_id: 'main' }, { message_id: 2, type: 'ui', params: tableSpec, agent_id: 'main' }];
    await page.reload();
    await page.locator('.gen-ui-table-wrap').waitFor();
    assert.equal(await page.locator('.gen-ui-table-wrap tbody tr').count(), 3);
    await page.getByRole('button', { name: 'Show cost per seat as a chart' }).click();
    await page.locator('.gen-ui-chart').waitFor();
    assert.deepEqual(actions[0], { ui_id: 'plan-table', action: 'show_cost_per_seat', payload: {} });
    assert.deepEqual(await page.locator('.gen-ui-chart-value').allTextContents(), ['6', '5', '4']);
    assert.equal(await page.locator('.gen-ui-table-wrap tbody tr').count(), 3);
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-table-chart-v1.png'), fullPage: true });
    await page.reload();
    await page.locator('.gen-ui-chart').waitFor();
    assert.deepEqual(await page.locator('.gen-ui-chart-value').allTextContents(), ['6', '5', '4']);
    assert.deepEqual(errors, []);
    const failure = await browser.newPage();
    await failure.route('https://esm.sh/**', route => route.abort());
    entries = [{ message_id: 1, type: 'user', content: 'Explore aircraft rotation', agent_id: 'main' }, { message_id: 2, type: 'ui', params: spec, agent_id: 'main' }];
    await failure.goto(`http://127.0.0.1:${server.address().port}/sessions?s=intelligent-ui`);
    await failure.frameLocator('iframe').locator('#status[data-error="true"]').waitFor();
    await failure.close();
    console.log('PASS: real Three.js/WebGL, correct axis rotations, reset, stable demonstration camera, drag orbit, reduced motion, mobile, readable import failure, table → action → streamed chart, retained source table and history replay');
  } finally {
    await browser.close(); server.close(); fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

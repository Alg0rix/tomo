/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/runtime_logs.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const html = execFileSync(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', `
from jinja2 import Environment, FileSystemLoader
from types import SimpleNamespace
from app.plugins.icons import ICONS
env = Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
env.globals.update(plugin_icons=ICONS, url_for=lambda name, **kwargs: '/static/' + kwargs.get('path', ''), avatar_color=lambda *args: '#777', eval_ui_enabled=False)
print(env.get_template('system.html').render(brand='Tomo', page='system', current_role='admin', app_version='test', static_ver='test', settings={}, llm_profiles=[], tools=[], plugins=[], mcp_servers=[], shared_channels=[], users=[], request=SimpleNamespace(session={'username': 'test'})))
`], { cwd: root, encoding: 'utf8' }).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');

(async () => {
  const streams = new Set();
  const queries = [];
  let sequence = 0;
  const example = { timestamp: '2026-01-01T09:10:11+00:00', level: 'ERROR', type: 'plugin', message: '<img src=x onerror=alert(1)> Hook failed', event: 'failed', session_id: 's1', exception: 'Traceback: synthetic plugin failure' };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://test');
    if (url.pathname === '/api/logs/stream') {
      queries.push(url.searchParams);
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      streams.add(res);
      res.on('close', () => streams.delete(res));
      const record = { ...example, type: url.searchParams.get('type') || 'plugin' };
      res.write('event: snapshot\ndata: ' + JSON.stringify([{ id: 'snapshot', record }]) + '\n\n');
    } else if (url.pathname === '/api/logs') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"records":[]}');
    } else { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(html); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}/system#logs`);
      await page.addStyleTag({ path: path.join(root, 'app/static/css/tomo.css') });
      await page.addStyleTag({ path: path.join(root, 'app/static/css/logs.css') });
      await page.evaluate(() => { window.Tomo = { api: async () => null, toast: () => {}, escapeHtml: String }; });
      await page.addScriptTag({ path: path.join(root, 'app/static/js/system.js') });
      await page.addScriptTag({ path: path.join(root, 'app/static/js/logs.js') });
      await page.waitForFunction(() => document.getElementById('logStatus').textContent === 'Live');
      await page.locator('.runtime-log-record').waitFor();
      assert.equal(await page.locator('.sys-section:visible').count(), 1);
      assert.equal(await page.locator('#logRecords img').count(), 0, 'Log content must never render HTML');
      assert.match(await page.locator('.runtime-log-message').textContent(), /<img/);
      await page.locator('.runtime-log-record summary').click();
      assert.match(await page.locator('.runtime-log-record pre').textContent(), /Traceback/);
      await page.locator('#logFilters select[name=type]').selectOption('llm');
      await page.locator('#logFilters input[name=session]').fill('s1');
      await page.locator('#logFilters button').click();
      await page.waitForFunction(() => document.querySelector('.runtime-log-type')?.textContent === 'llm');
      assert.equal(queries.at(-1).get('session'), 's1');
      await page.locator('#logPause').click();
      await page.waitForFunction(() => document.getElementById('logPause').textContent === 'Resume');
      assert.match(await page.locator('#logStatus').textContent(), /Paused/);
      await page.locator('#logPause').click();
      await page.waitForFunction(() => document.getElementById('logStatus').textContent === 'Live');
      await page.locator('#logClear').click();
      assert.equal(await page.locator('.runtime-log-record').count(), 0);
      for (const response of streams) response.write('event: log\ndata: ' + JSON.stringify({ id: 'live-' + sequence++, record: { ...example, type: 'llm', message: 'LLM request failed', provider: 'OpenAICompat', duration_ms: 2500 } }) + '\n\n');
      await page.locator('.runtime-log-record').waitFor();
      await page.locator('.runtime-log-record summary').click();
      const tooWide = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      assert.equal(tooWide, false, 'No horizontal overflow on mobile');
      await page.locator('#machineBay').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(process.env.BB_THREAD_STORAGE || '/tmp', `tomo-runtime-logs-${width}.png`) });
      await page.locator('#systemNav [data-section=general]').click();
      await page.waitForFunction(() => document.getElementById('sec-logs').hidden);
      await page.waitForTimeout(100);
      assert.equal(streams.size, 0, 'Navigating away closes the log stream');
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`${width}px: native SSE, safe content, filters, details, pause/resume, clear and navigation passed`);
    }
  } finally {
    await browser.close();
    for (const response of streams) response.end();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

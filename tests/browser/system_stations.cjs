/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/system_stations.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
// Render the complete template: testing the memory partial alone misses markup
// problems in surrounding stations that can move a form outside its container.
const html = execFileSync(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', `
from jinja2 import Environment, FileSystemLoader
from types import SimpleNamespace
from app.plugins.icons import ICONS
env = Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
env.globals.update(plugin_icons=ICONS, url_for=lambda name, **kwargs: '/static/' + kwargs.get('path', ''), avatar_color=lambda *args: '#777', eval_ui_enabled=False)
print(env.get_template('system.html').render(brand='Tomo', page='system', app_version='test', static_ver='test', settings={}, llm_profiles=[], tools=[], plugins=[], mcp_servers=[], shared_channels=[], users=[], request=SimpleNamespace(session={'username': 'test'})))
`], { cwd: root, encoding: 'utf8' })
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
  .replace(/<link\b[^>]*>/gi, '');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://system.test/**', route => route.fulfill({ contentType: 'text/html', body: html }));
      async function load(hash = '') {
        await page.goto('about:blank');
        await page.goto('http://system.test/system' + hash);
        await page.addStyleTag({ path: path.join(root, 'app/static/css/tomo.css') });
        assert.equal(await page.locator('#vaultFactForm').isVisible(), false, 'No memory form flash before JS');
        assert.equal(await page.locator('#vaultFactForm').evaluate(el => el.closest('.sys-section').id), 'sec-memory');
        await page.evaluate(() => { window.Tomo = { api: async () => null, toast: () => {}, escapeHtml: value => String(value) }; });
        await page.addScriptTag({ path: path.join(root, 'app/static/js/system.js') });
      }
      await load();
      assert.equal(await page.locator('.sys-section:visible').count(), 1);
      assert.equal(await page.locator('#sec-general').isVisible(), true);
      for (const station of ['memory', 'general', 'models', 'memory', 'tools', 'mcp', 'plugins', 'shared_channel', 'users', 'general']) {
        await page.locator(`#systemNav a[data-section="${station}"]`).click();
        await page.waitForFunction(expected => {
          const section = document.getElementById('sec-' + expected);
          return !section.hidden && getComputedStyle(section).display !== 'none';
        }, station);
        assert.equal(await page.locator('.sys-section:visible').count(), 1, `Only ${station} is visible`);
        assert.equal(await page.locator('#vaultFactForm').isVisible(), station === 'memory');
        assert.equal(await page.locator('#vaultUpload').isVisible(), station === 'memory');
        assert.equal(await page.locator('#sec-memory a[href="/memory"]').isVisible(), station === 'memory');
      }
      await page.locator('#systemNav a[data-section="mcp"]').click();
      await page.evaluate(() => {
        window.Tomo.api = async (url, opts) => {
          if (opts?.body) {
            window.savedMcp = { id: 'test', ...JSON.parse(opts.body) };
            return window.savedMcp;
          }
          return url.endsWith('/test') ? { ...window.savedMcp, items: [] } : { servers: [] };
        };
      });
      await page.addScriptTag({ path: path.join(root, 'app/static/js/mcp.js') });
      await page.locator('#addMcpServerBtn').click();
      assert.equal(await page.locator('#mcpParallel').isChecked(), false);
      await page.locator('#mcpName').fill('Test');
      await page.locator('#mcpCommand').fill('echo');
      await page.locator('label.toggle:has(#mcpParallel)').click();
      await page.locator('#mcpSave').click();
      await page.waitForFunction(() => window.savedMcp?.supports_parallel_tool_calls === true);
      assert.equal(await page.locator('#mcpParallel').isChecked(), true);
      await page.screenshot({ path: `/tmp/tomo-mcp-parallel-${width}.png` });
      await load('#memory');
      assert.equal(await page.locator('#vaultFactForm').isVisible(), true, 'Direct Memory link works');
      await page.evaluate(() => { location.hash = 'general'; });
      await page.waitForFunction(() => document.getElementById('sec-memory').hidden);
      await page.goBack();
      await page.waitForFunction(() => !document.getElementById('sec-memory').hidden);
      await load('#invalid-station');
      assert.equal(await page.locator('#sec-general').isVisible(), true);
      assert.equal(await page.locator('#vaultFactForm').isVisible(), false);
      assert.deepEqual(errors, []);
      console.log(`${width}px: initial render, all stations, Memory deep link, back navigation and invalid hash passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

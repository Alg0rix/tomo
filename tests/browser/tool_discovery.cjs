/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/tool_discovery.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://discovery.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    await page.goto('http://discovery.test/');
    await page.setContent('<div class="chat-wrap"><div class="composer is-generating"></div><div class="chat-scroll"><div class="turn" id="main"></div></div><div class="si-timeline" id="inspector"></div></div>');
    for (const file of ['tomo.css', 'chat.css']) {
      await page.addStyleTag({ path: path.join(root, 'app/static/css', file) });
    }
    for (const file of ['tomo.js', 'chat_worknotes.js', 'chat_turn_stream.js']) {
      await page.addScriptTag({ path: path.join(root, 'app/static/js', file) });
    }
    await page.evaluate(() => {
      const noop = () => {};
      const es = window.discoveryEvents = new EventTarget();
      window.discoveryStream = TomoTurnStream.attach(es, {
        mode: 'live', wrap: document.querySelector('.chat-wrap'), turn: document.getElementById('main'),
        defaultAgentName: 'Tomo', agentId: 'main', esc: Tomo.escapeHtml,
        agentColor: () => '', setSending: noop, setStatus: noop,
        busyStatusLabel: () => 'Working', refreshSendBtn: noop, atBottom: noop,
      });
      es.dispatchEvent(new MessageEvent('tool', { data: JSON.stringify({
        tool: 'search_tools', call_id: 'discover', args: { query: 'expense' },
      }) }));
    });
    const card = page.locator('#main .tool[data-call-id="discover"]');
    await page.waitForFunction(() => document.querySelector('#main .work-title')?.textContent.includes('Finding relevant tools…'));
    assert.equal(await card.locator('.tname').textContent(), 'Finding relevant tools…');
    assert.equal(await card.locator('.tool-head').getAttribute('aria-expanded'), 'false');
    await page.evaluate(() => discoveryEvents.dispatchEvent(new MessageEvent('tool_result', {
      data: JSON.stringify({ tool: 'search_tools', call_id: 'discover', error: false, result: JSON.stringify({
        loaded_tools: ['plugin__money__expense', 'plugin__money__report', 'plugin__money__budget'],
        total_matches: 3, schema_tokens: 7000, schema_target_tokens: 6400, over_target: true,
      }) }),
    })));
    await page.waitForFunction(() => document.querySelector('#main .work-title')?.textContent.includes('Loaded 3 tools'));
    assert.equal(await card.locator('.tname').textContent(), 'Loaded 3 tools');
    assert.equal(await card.locator('.tchip').textContent(), '');
    assert.equal(await card.locator('.tool-head').getAttribute('aria-expanded'), 'false');
    assert.equal(await card.locator('.tool-discovery-details').isVisible(), false);
    await card.locator('.tool-head').click();
    assert.equal(await card.locator('.tool-discovery-details').isVisible(), true);
    assert.match(await card.locator('.tool-discovery-details').textContent(), /7,000 tokens.*6,400-token soft target/);
    assert.match(await card.locator('.tool-discovery-details').textContent(), /Above target; tools remain available/);
    assert.equal(await card.evaluate(el => el.classList.contains('error')), false);

    const labels = await page.evaluate(() => {
      const host = document.getElementById('inspector');
      const cases = [
        [{ loaded_tools: ['one'], total_matches: 1 }, false],
        [{ loaded_tools: [], total_matches: 0 }, false],
        [{ loaded_tools: [], total_matches: 1, budget_limited: true }, false],
        ['not json', false],
        ['Error: Search unavailable', true],
        [{ loaded_tools: ['<img src=x onerror="window.injected=true">'], schema_tokens: 100, schema_target_tokens: null }, false],
      ];
      return cases.map(([data, error], i) => {
        const tool = Tomo.buildToolCard({ tool: 'search_tools', args: {}, call_id: 'history-' + i });
        host.appendChild(tool);
        Tomo.finishToolCard(tool, typeof data === 'string' ? data : JSON.stringify(data), error);
        return tool.querySelector('.tname').textContent;
      });
    });
    assert.deepEqual(labels, ['Loaded 1 tool', 'No matching tools', 'No tools loaded', 'Tool discovery completed', 'Tool discovery failed', 'Loaded 1 tool']);
    assert.equal(await page.evaluate(() => !!window.injected), false);
    assert.equal(await page.locator('#inspector .tool img').count(), 0);
    assert.match(await page.locator('[data-call-id="history-5"] .tool-discovery-details').textContent(), /Target unavailable/);
    await page.evaluate(() => {
      const tool = Tomo.buildToolCard('read_file', { path: 'report.txt' });
      document.getElementById('inspector').appendChild(tool);
      Tomo.finishToolCard(tool, 'File content', false);
    });
    assert.equal(await page.locator('#inspector [data-tool-name="read_file"] .tname').textContent(), 'read_file');
    assert.deepEqual(errors, []);
    console.log('Tool discovery UX: live status, history, inspectors, soft target, empty/error results, and safe rendering passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

/* NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui_lifecycle.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const server = http.createServer((req, res) => {
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html');
      return res.end('<!doctype html><div id="wrap"><div id="scroll"><div class="turn" id="turn"></div></div></div>');
    }
    res.setHeader('Content-Type', 'text/javascript');
    res.end(fs.readFileSync(path.join(root, 'app', req.url)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    for (const file of ['tomo.js', 'intelligent_ui.js', 'generative_ui.js', 'chat_turn_stream.js']) {
      await page.addScriptTag({ url: `/static/js/${file}` });
    }
    await page.evaluate(() => {
      const noop = () => {};
      const listeners = {};
      window.emit = (type, data) => (listeners[type] || []).forEach(fn => fn({ data: JSON.stringify(data) }));
      window.actions = [];
      window.stream = TomoTurnStream.attach({ addEventListener: (type, fn) => (listeners[type] ||= []).push(fn) }, {
        mode: 'live', wrap: document.getElementById('wrap'), scroll: document.getElementById('scroll'), turn: document.getElementById('turn'),
        defaultAgentName: 'Tomo', agentId: 'main', esc: Tomo.escapeHtml, agentColor: () => '',
        bubbleHtml: () => '<div class="msg assistant"><div class="bubble-body"></div></div>',
        setMarkdown: (el, text) => { el.textContent = text; },
        atBottom: noop, setStatus: noop, refreshSendBtn: noop, onBindHitl: noop,
        busyStatusLabel: () => 'Working', closeStream: noop, finishTurn: () => { window.finished = true; },
        currentSessionId: () => 'lifecycle', dispatchUiAction: action => actions.push(action),
      });
      window.nativeSpec = {
        ui_id: 'native', state: { amount: 5, choice: 'a' },
        tree: { type: 'stack', children: [
          { type: 'input', id: 'amount' },
          { type: 'select', id: 'choice', options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
          { type: 'button', label: 'Submit', action: 'submit' },
        ] },
      };
      window.sandboxSpec = {
        ui_id: 'sandbox', tree: { type: 'sandbox', html: '<input id="amount" value="5"><button id="send">Send</button>',
          jsExpressions: "window.instance = Math.random(); document.getElementById('send').onclick = () => sendPrompt({text: document.getElementById('amount').value});" },
      };
      emit('ui', nativeSpec);
      emit('ui', nativeSpec);
      emit('ui', sandboxSpec);
    });
    const native = page.locator('[data-ui-id="native"].gen-ui');
    await native.locator('input').fill('9');
    await native.locator('select').selectOption('b');
    const persisted = () => page.evaluate(() => {
      const root = document.querySelector('.gen-ui[data-ui-id="native"]');
      return { state: root._genState, stored: JSON.parse(sessionStorage.getItem(root._genStorageKey)).state };
    });
    assert.deepEqual(await persisted(), { state: { amount: '9', choice: 'b' }, stored: { amount: '9', choice: 'b' } });
    await native.locator('input').evaluate(el => { window.originalInput = el; });
    const frame = page.frameLocator('[data-ui-id="sandbox"] iframe');
    await frame.locator('#amount').fill('9');
    const instance = await frame.locator('#amount').evaluate(() => window.instance);
    const checkControls = async () => {
      assert.equal(await frame.locator('#amount').inputValue(), '9');
      assert.equal(await frame.locator('#amount').evaluate(() => window.instance), instance);
      assert.equal(await native.locator('input').inputValue(), '9');
      assert.equal(await native.locator('input').evaluate(el => el === window.originalInput), true);
      assert.deepEqual(await persisted(), { state: { amount: '9', choice: 'b' }, stored: { amount: '9', choice: 'b' } });
    };
    // Exercise the real stream handlers, including fallback tool-result delivery.
    await page.evaluate(() => { emit('ui', nativeSpec); emit('ui', sandboxSpec); });
    await checkControls();
    await native.locator('input').fill('10');
    assert.equal((await persisted()).stored.amount, '10');
    await native.locator('input').fill('9');
    await page.evaluate(() => {
      emit('tool', { tool: 'render_ui', call_id: 'render', args: sandboxSpec });
      emit('tool_result', { tool: 'render_ui', call_id: 'render', result: JSON.stringify(sandboxSpec) });
      emit('delta', { content: 'Adjust these controls.' });
      emit('ui', { ui_id: 'second', tree: { type: 'text', value: 'Another answer' } });
    });
    await checkControls();
    await page.evaluate(() => emit('done', { content: 'Adjust these controls.' }));
    await checkControls();
    assert.deepEqual(await page.locator('#turn').evaluate(el => Array.from(el.children, child => child.matches('.msg.assistant') ? 'answer' : child.dataset.uiId || (child.matches('.tool') ? 'tool' : child.className))), ['tool', 'answer', 'native', 'sandbox', 'second']);
    await page.evaluate(() => emit('turn.end', {}));
    assert.equal(await page.evaluate(() => finished), true);
    await checkControls();
    await native.locator('button').click();
    await frame.locator('#send').click();
    await page.waitForFunction(() => actions.length === 2);
    assert.deepEqual(await page.evaluate(() => actions), [
      { ui_id: 'native', action: 'submit', payload: { amount: '9', choice: 'b' } },
      { ui_id: 'sandbox', action: 'sendPrompt', payload: { text: '9' } },
    ]);
    // Restore native state from storage; explicit patches must still update it.
    await page.evaluate(() => {
      document.querySelector('.gen-ui-block[data-ui-id="native"]').remove();
      TomoGenerativeUI.mount(document.getElementById('turn'), nativeSpec, { sessionId: 'lifecycle', asBlock: true });
    });
    assert.equal(await native.locator('input').inputValue(), '9');
    await page.evaluate(() => TomoGenerativeUI.mount(document.getElementById('turn'), { ui_id: 'native', mode: 'patch', patch: [{ op: 'replace', path: '/state/amount', value: '12' }] }, { sessionId: 'lifecycle', asBlock: true }));
    assert.equal(await native.locator('input').inputValue(), '12');
    await native.locator('input').fill('13');
    assert.equal((await persisted()).stored.amount, '13');
    assert.deepEqual(errors, []);
    console.log('PASS: native duplicate persistence, local-edit deduplication, storage restore, state patches, iframe identity through duplicate UI/tool delivery and actual stream completion');
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

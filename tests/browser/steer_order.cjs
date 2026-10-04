/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/steer_order.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://steer.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    await page.goto('http://steer.test/');
    await page.setContent('<div id="wrap"><div id="scroll"><div class="turn" id="original"></div></div></div>');
    for (const file of ['tomo.js', 'chat_turn_stream.js']) {
      await page.addScriptTag({ path: path.join(root, 'app/static/js', file) });
    }
    const result = await page.evaluate(() => {
      const noop = () => {};
      const listeners = {};
      const emit = (type, data) => (listeners[type] || []).forEach(fn => fn({ data: JSON.stringify(data) }));
      const scroll = document.getElementById('scroll');
      const addUser = (text, queued, attachments, pending = false) => {
        const turn = document.createElement('div');
        turn.className = 'turn';
        turn.innerHTML = '<div class="msg user"><div class="bubble-body"></div></div>';
        const bubble = turn.firstElementChild;
        const body = bubble.firstElementChild;
        body.dataset.raw = text;
        body.textContent = text;
        if (pending) bubble.classList.add('msg-steering');
        scroll.appendChild(turn);
        return bubble;
      };
      const attachment = TomoTurnStream.attach({ addEventListener: (type, fn) => (listeners[type] ||= []).push(fn) }, {
        mode: 'live', wrap: document.getElementById('wrap'), scroll, turn: document.getElementById('original'),
        defaultAgentName: 'Tomo', agentId: 'main', esc: Tomo.escapeHtml, agentColor: () => '',
        bubbleHtml: () => '<div class="msg assistant"><div class="bubble-body"></div></div>',
        appendUserBubble: addUser, setMarkdown: (el, text) => { el.textContent = text; },
        atBottom: noop, setStatus: noop, refreshSendBtn: noop, onBindHitl: noop,
        currentSessionId: () => 'test',
      });
      emit('delta', { content: 'Before steer' });
      emit('tool', { tool: 'bash', call_id: 'old', args: {} });
      const first = addUser('Change direction', false, [], true);
      // Consumption arrives before the POST acceptance response.
      emit('user', { steered: true, steer_id: 'first', content: 'Change direction' });
      attachment.continueAfterUser(first);
      emit('delta', { content: 'After first steer' });
      emit('tool', { tool: 'bash', call_id: 'new', args: {} });
      emit('tool_result', { tool: 'bash', call_id: 'old', result: 'Old tool finished' });
      const oldResult = document.querySelector('[data-call-id="old"]')._res.textContent;
      emit('tool_result', { tool: 'bash', call_id: 'missing', result: 'Unmatched result' });
      const newRunning = document.querySelector('[data-call-id="new"]').classList.contains('loading');
      const second = addUser('Also this');
      second.dataset.steerId = 'second';
      attachment.continueAfterUser(second);
      attachment.continueAfterUser(first); // Delayed receipt must not move backwards.
      emit('user', { steered: true, steer_id: 'second', content: 'Also this' });
      emit('delta', { content: 'After second steer' });
      emit('user', { steered: true, steer_id: 'remote', content: 'From another tab' });
      emit('delta', { content: 'After remote steer' });
      const turns = Array.from(scroll.children, t => t.textContent);
      const users = scroll.querySelectorAll('.msg.user').length;
      attachment.dispose();
      return { turns, users, oldResult, newRunning };
    });
    assert.equal(result.users, 3);
    assert.match(result.turns[0], /Before steer/);
    assert.doesNotMatch(result.turns[0], /After first steer/);
    assert.match(result.turns[1], /Change direction.*After first steer/);
    assert.match(result.turns[2], /Also this.*After second steer/);
    assert.match(result.turns[3], /From another tab.*After remote steer/);
    assert.equal(result.oldResult, 'Old tool finished');
    assert.equal(result.newRunning, true);
    assert.deepEqual(errors, []);
    console.log('Steer ordering passed: local, rapid, remote, and late tool results');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

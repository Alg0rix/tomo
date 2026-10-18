/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/queue_drain.cjs */
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
    await page.route('http://queue.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    await page.goto('http://queue.test/');
    await page.setContent('<div id="wrap"><div id="scroll"><div class="turn" id="original"></div></div></div>');
    for (const file of ['tomo.js', 'chat_turn_stream.js']) {
      await page.addScriptTag({ path: path.join(root, 'app/static/js', file) });
    }
    const result = await page.evaluate(() => {
      const noop = () => {};
      const run = (queue) => {
        const listeners = {};
        const calls = { finishTurn: 0, sending: null };
        const attachment = TomoTurnStream.attach({ addEventListener: (type, fn) => (listeners[type] ||= []).push(fn) }, {
          mode: 'resume', wrap: document.getElementById('wrap'), scroll: document.getElementById('scroll'),
          turn: document.getElementById('original'), defaultAgentName: 'Tomo', agentId: 'main',
          esc: Tomo.escapeHtml, agentColor: () => '',
          bubbleHtml: () => '<div class="msg assistant"><div class="bubble-body"></div></div>',
          appendUserBubble: noop, setMarkdown: noop, atBottom: noop, setStatus: noop, refreshSendBtn: noop,
          onBindHitl: noop, currentSessionId: () => 'test', closeStream: noop, closeTransport: noop,
          busyStatusLabel: () => 'busy', messageQueue: queue,
          finishTurn: () => { calls.finishTurn += 1; },
          setSending: (v) => { calls.sending = v; },
        });
        // Reconnected listen after the turn already ended: heartbeats only.
        (listeners.heartbeat || []).forEach(fn => fn({ data: '{}' }));
        attachment.dispose && attachment.dispose();
        return calls;
      };
      return { queued: run([{ text: 'follow-up' }]), empty: run([]) };
    });
    // A missed turn.end must still drain queued follow-ups.
    assert.equal(result.queued.finishTurn, 1);
    assert.equal(result.empty.finishTurn, 0);
    assert.equal(result.empty.sending, false);
    assert.deepEqual(errors, []);
    console.log('Queue drain passed: idle resume sends queued follow-ups');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

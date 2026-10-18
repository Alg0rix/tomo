/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/queue_park.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  try {
    const page = await browser.newPage();
    const errors = [];
    const streams = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://park.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    await page.route('http://park.test/api/**', route => {
      const url = route.request().url();
      if (url.includes('/chat/stream')) streams.push(JSON.parse(route.request().postData() || '{}').message);
      route.fulfill({ status: url.includes('/chat/stream') ? 500 : 200, contentType: 'application/json', body: '{}' });
    });
    await page.goto('http://park.test/');
    await page.setContent(`<div class="chat-wrap" data-session-id="s1">
      <div class="chat-scroll"></div><textarea class="chat-input"></textarea>
      <button class="chat-send"></button><span class="chat-status"></span></div>`);
    for (const file of ['tomo.js', 'markdown.js', 'chat_turn_stream.js', 'chat.js']) {
      await page.addScriptTag({ path: path.join(root, 'app/static/js', file) });
    }
    const key = 'tomo:chat-queue:s1';
    const parked = [{ text: 'first follow-up', attachmentIds: [], attachments: [] }, { text: 'second', attachmentIds: [], attachments: [] }];

    // Switching away mid-queue parks it; reopening restores the bubbles.
    const roundTrip = await page.evaluate(({ key, parked }) => {
      const wrap = document.querySelector('.chat-wrap');
      wrap._tomoChatHandle.destroy();
      sessionStorage.setItem(key, JSON.stringify(parked));
      TomoChat.init(wrap);
      const restored = [...wrap.querySelectorAll('.msg.user')].map(el => el.textContent.trim());
      const storedAfterRestore = sessionStorage.getItem(key);
      wrap._tomoChatHandle.destroy();
      return { restored, storedAfterRestore, reparked: JSON.parse(sessionStorage.getItem(key) || '[]').map(q => q.text) };
    }, { key, parked });
    assert.ok(roundTrip.restored.some(t => t.includes('first follow-up')), JSON.stringify(roundTrip));
    assert.ok(roundTrip.restored.some(t => t.includes('second')));
    assert.equal(roundTrip.storedAfterRestore, null);
    assert.deepEqual(roundTrip.reparked, ['first follow-up', 'second']);

    // Reopened and idle: the parked queue drains in order.
    await page.evaluate(() => {
      const wrap = document.querySelector('.chat-wrap');
      wrap.querySelector('.chat-scroll').innerHTML = '';
      TomoChat.init(wrap);
    });
    await page.waitForTimeout(1500);
    assert.equal(streams[0], 'first follow-up');
    assert.deepEqual(errors, []);
    console.log('Queue park passed: queued follow-ups survive a chat switch');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/context_usage.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<div id="chat" data-session-id="one"><button class="ctx-usage-trigger"><span class="ctx-usage-ring"></span><span class="ctx-usage-pct"></span></button></div>');
    await page.evaluate(() => {
      window.requests = [];
      window.Tomo = {
        escapeHtml: value => String(value),
        api: url => new Promise(resolve => window.requests.push({ url, resolve })),
      };
    });
    await page.addScriptTag({ path: path.resolve(__dirname, '../../app/static/js/context_usage.js') });
    await page.evaluate(() => window.TomoContextUsage.init(document.querySelector('#chat')));
    assert.equal(await page.evaluate(() => window.requests.length), 1);

    // Switching the model refreshes the same session URL. A slow response
    // from before the switch must not overwrite the newly resolved limit.
    await page.evaluate(() => window.TomoContextUsage.refresh(document.querySelector('#chat')));
    await page.evaluate(() => window.requests[1].resolve({ used: 500_000, limit: 1_000_000, percent: 50, prompt_budget: 941_808, compressed: true }));
    const trigger = page.locator('.ctx-usage-trigger');
    await page.waitForFunction(() => document.querySelector('.ctx-usage-trigger').title === '500K / 1M tokens');
    await page.evaluate(() => window.requests[0].resolve({ used: 500_000, limit: 128_000, percent: 100 }));
    assert.equal(await trigger.getAttribute('title'), '500K / 1M tokens');
    assert.equal(await page.locator('.ctx-usage-pct').textContent(), '50%');
    assert.match(await page.locator('.ctx-pop-budget').textContent(), /compacted.*Prompt budget/);

    // A real smaller window is still shown honestly.
    await page.evaluate(() => window.TomoContextUsage.refresh(document.querySelector('#chat')));
    await page.evaluate(() => window.requests[2].resolve({ used: 500_000, limit: 128_000, percent: 100, blocked: true, compaction_error: 'Latest request cannot fit.' }));
    await page.waitForFunction(() => document.querySelector('.ctx-usage-trigger').title === '500K / 128K tokens');
    assert.equal(await page.locator('.ctx-pop-budget').textContent(), 'Latest request cannot fit.');

    // Ignore a response for a session that is no longer visible.
    await page.evaluate(() => {
      const wrap = document.querySelector('#chat');
      window.TomoContextUsage.refresh(wrap);
      wrap.dataset.sessionId = 'two';
      window.requests[3].resolve({ used: 10, limit: 1000, percent: 1 });
    });
    assert.equal(await trigger.getAttribute('title'), '500K / 128K tokens');

    await page.evaluate(() => {
      const wrap = document.querySelector('#chat');
      window.TomoContextUsage.refresh(wrap);
      window.TomoContextUsage.destroy(wrap);
      window.requests[4].resolve({ used: 10, limit: 1000, percent: 1 });
      window.TomoContextUsage.refresh(wrap);
    });
    assert.equal(await page.evaluate(() => window.requests.length), 5);
    assert.equal(await page.locator('.ctx-usage-popover').count(), 0);
    console.log('Context refresh, out-of-order responses, smaller windows, session changes and cleanup passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

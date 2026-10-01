/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/chat_rail_pointer.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    // At <=900px artifacts intentionally cover the shell as a mobile overlay.
    for (const width of [960, 1280]) {
      for (const open of [false, true]) {
        const page = await browser.newPage({ viewport: { width, height: 840 }, reducedMotion: 'reduce' });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.setContent(`<!doctype html><html data-theme="dark"><body>
          <div id="sessionList" hidden></div><div id="sessionEmpty" hidden></div>
          <div id="sessionChat" class="chat-wrap sessions-chat" style="height:800px;width:100%">
            <main class="chat-main">
              <nav id="chatQueryRail" class="chat-query-rail" aria-label="User messages" hidden></nav>
              <div class="chat-scroll" style="height:100%">
                <button id="chatAction" style="position:absolute;left:100px;top:45%">Copy message</button>
                <button id="toolAction" style="position:absolute;left:100px;top:55%">Expand tool</button>
                <textarea id="composerInput" style="position:absolute;left:100px;top:62%;width:160px;height:36px" aria-label="Message"></textarea>
              </div>
            </main>
            <aside class="chat-agent-panel" data-cap-open="${open ? '1' : '0'}">
              <button id="artifactAction">Artifact preview</button>
            </aside>
          </div></body></html>`);
        for (const css of ['tomo.css', 'chat.css']) {
          await page.addStyleTag({ path: path.join(root, 'app/static/css', css) });
        }
        await page.evaluate(() => { window.Tomo = { api: async () => null }; });
        await page.addScriptTag({ path: path.join(root, 'app/static/js/sessions.js') });
        await page.evaluate(() => {
          window.clickCounts = {};
          const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
          HTMLElement.prototype.scrollIntoView = function (options) {
            if (this.classList.contains('turn')) window.jumpedQuery = this.dataset.queryId;
            originalScrollIntoView.call(this, options);
          };
          for (const id of ['chatAction', 'toolAction', 'artifactAction']) {
            document.getElementById(id).onclick = () => { window.clickCounts[id] = (window.clickCounts[id] || 0) + 1; };
          }
          window.addQuery = (index, response = '') => {
            const wrap = document.getElementById('sessionChat');
            const turn = document.createElement('div');
            turn.className = 'turn';
            turn.dataset.queryId = `chat-query-${index}`;
            turn.style.height = '40px';
            turn.innerHTML = '<div class="msg user">User prompt</div>';
            if (response) {
              const msg = document.createElement('div');
              msg.className = 'msg assistant';
              msg.innerHTML = '<div class="bubble-body"></div>';
              msg.firstChild.textContent = response;
              turn.appendChild(msg);
            }
            wrap.querySelector('.chat-scroll').appendChild(turn);
            wrap.dispatchEvent(new CustomEvent('tomo:user-turn', { detail: { queryId: turn.dataset.queryId, queryIndex: index, text: index === 3 ? 'gas' : `Message ${index + 1}` } }));
          };
          for (let i = 0; i < 4; i++) window.addQuery(i, i === 3 ? 'Lanjut, hasilnya udah jadi.' : 'Earlier response');
        });
        await page.waitForFunction(() => document.querySelector('.chat-query-item[data-query-id="chat-query-3"]').dataset.context.includes('hasilnya'));
        const hit = await page.locator('#chatAction').evaluate(el => {
          const r = el.getBoundingClientRect();
          const target = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          return { id: target.id, className: target.className };
        });
        assert.equal(hit.id, 'chatAction', `Chat click intercepted at ${width}px, panel=${open}: ${JSON.stringify(hit)}`);
        await page.locator('#chatAction').hover();
        assert.equal(await page.locator('.chat-query-rail').evaluate(el => el.matches(':hover')), false);
        await page.locator('#chatAction').click();
        await page.locator('#toolAction').click();
        await page.locator('#composerInput').fill('Still interactive');
        assert.equal(await page.locator('#composerInput').inputValue(), 'Still interactive');
        if (open) await page.locator('#artifactAction').click();
        const item = page.locator('.chat-query-item').nth(3);
        await item.hover();
        await page.locator('#chatQueryPreview').waitFor({ state: 'visible' });
        assert.match(await page.locator('.chat-query-context').textContent(), /hasilnya udah jadi/);
        assert.match(await page.locator('.chat-query-meta').textContent(), /Message 4 \/ 4 · Response/);
        assert.doesNotMatch(await page.locator('#chatQueryPreview').textContent(), /Waiting for a response/);
        const previewBounds = await page.locator('#chatQueryPreview').boundingBox();
        const mainBounds = await page.locator('.chat-main').boundingBox();
        assert.ok(previewBounds.x + previewBounds.width <= mainBounds.x + mainBounds.width, 'Preview fits chat beside artifact');
        if (width === 1280 && process.env.RAIL_SCREENSHOT) {
          await page.screenshot({ path: process.env.RAIL_SCREENSHOT + (open ? '.open.png' : '.closed.png') });
        }
        // Even the visible preview cannot intercept an underlying chat action.
        await page.locator('#chatAction').click();
        await item.focus();
        await item.press('ArrowUp');
        assert.equal(await page.locator('.chat-query-item').nth(2).evaluate(el => el === document.activeElement), true);
        await page.keyboard.press('Home');
        await page.keyboard.press('Enter');
        assert.equal(await page.evaluate(() => window.jumpedQuery), 'chat-query-0');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#chatQueryPreview').isVisible(), false);
        assert.equal(await page.evaluate(() => window.clickCounts.chatAction), 2);
        assert.equal(await page.evaluate(() => window.clickCounts.toolAction), 1);

        // Stream a response into a new turn while its preview is already open.
        await page.evaluate(() => {
          document.getElementById('sessionChat').dataset.liveStream = '1';
          window.addQuery(4);
        });
        const live = page.locator('.chat-query-item').nth(4);
        await live.hover();
        await page.waitForFunction(() => document.querySelector('#chatQueryPreview').dataset.state === 'working');
        await page.evaluate(() => {
          const turn = document.querySelector('.turn[data-query-id="chat-query-4"]');
          turn.insertAdjacentHTML('beforeend', '<div class="msg assistant streaming"><div class="bubble-body">First streamed words</div></div>');
        });
        await page.waitForFunction(() => document.querySelector('.chat-query-context').textContent === 'First streamed words');
        assert.match(await page.locator('.chat-query-meta').textContent(), /Responding/);
        await page.evaluate(() => {
          const msg = document.querySelector('.turn[data-query-id="chat-query-4"] .msg.assistant');
          msg.querySelector('.bubble-body').firstChild.data = 'Complete answer';
          msg.classList.remove('streaming');
          const wrap = document.getElementById('sessionChat');
          delete wrap.dataset.liveStream;
          wrap.dispatchEvent(new CustomEvent('tomo:turn-end'));
        });
        await page.waitForFunction(() => document.querySelector('.chat-query-context').textContent === 'Complete answer');
        assert.match(await page.locator('.chat-query-meta').textContent(), /· Response/);
        // Completed tool-only turns must not claim they're still waiting.
        await page.evaluate(() => { window.addQuery(5); });
        await page.locator('.chat-query-item').nth(5).hover();
        await page.waitForFunction(() => document.querySelector('#chatQueryPreview').dataset.state === 'empty');
        assert.match(await page.locator('.chat-query-meta').textContent(), /No text response/);
        await page.evaluate(() => {
          document.querySelector('.turn[data-query-id="chat-query-5"] .msg.user').classList.add('msg-queued');
        });
        await page.waitForFunction(() => document.querySelector('#chatQueryPreview').dataset.state === 'queued');
        assert.match(await page.locator('.chat-query-context').textContent(), /queued for the next turn/);
        await page.evaluate(() => {
          const user = document.querySelector('.turn[data-query-id="chat-query-5"] .msg.user');
          user.classList.replace('msg-queued', 'msg-steering');
        });
        await page.waitForFunction(() => document.querySelector('#chatQueryPreview').dataset.state === 'steering');
        await page.evaluate(() => {
          const wrap = document.getElementById('sessionChat');
          wrap.dispatchEvent(new CustomEvent('tomo:user-turn-removed', { detail: { queryId: 'chat-query-5' } }));
          document.querySelector('.turn[data-query-id="chat-query-5"]').remove();
        });
        assert.equal(await page.locator('#chatQueryPreview').isVisible(), false);
        // Long rails still scroll only when the pointer is on a message button.
        await page.evaluate(() => { for (let i = 6; i < 40; i++) window.addQuery(i, 'More history'); });
        await page.locator('.chat-query-item').last().hover();
        await page.mouse.wheel(0, -250);
        await page.waitForFunction(() => document.querySelector('.chat-query-rail').scrollTop < document.querySelector('.chat-query-rail').scrollHeight - document.querySelector('.chat-query-rail').clientHeight - 10);
        assert.deepEqual(errors, []);
        console.log(`${width}px, artifacts ${open ? 'open' : 'closed'}: clicks, live previews, status, keyboard, scrolling passed`);
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

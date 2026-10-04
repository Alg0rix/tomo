/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/thinking_collapse.cjs */
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
    await page.route('http://thinking.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    await page.goto('http://thinking.test/');
    await page.setContent('<div class="chat-wrap"><div class="composer is-generating"></div><div class="chat-scroll"><div class="turn" id="main"></div></div><div class="si-timeline" id="inspector"></div></div>');
    for (const file of ['tomo.js', 'chat_worknotes.js', 'chat_turn_stream.js']) {
      await page.addScriptTag({ path: path.join(root, 'app/static/js', file) });
    }
    for (const host of ['main', 'inspector']) {
      const groups = page.locator('#' + host + ' > .work');
      await page.evaluate(id => {
        document.getElementById(id).appendChild(Tomo.buildReasoningCard('Thinking through the next step.'));
      }, host);
      await page.waitForFunction(id => document.querySelector('#' + id + ' > .work.is-open'), host);
      await groups.first().locator('.work-head').click();
      await page.evaluate(id => {
        const parent = document.getElementById(id);
        Tomo.updateReasoningCard(parent.querySelector('.si-think'), 'Updated streaming reasoning');
        const answer = document.createElement('div');
        answer.className = 'msg assistant';
        answer.textContent = 'Intermediate response';
        parent.appendChild(answer);
        parent.appendChild(Tomo.buildReasoningCard('Another thinking round'));
        const tool = document.createElement('div');
        tool.className = 'tool loading';
        parent.appendChild(tool);
      }, host);
      await page.waitForFunction(id => document.querySelectorAll('#' + id + ' > .work').length === 2, host);
      assert.deepEqual(await groups.evaluateAll(nodes => nodes.map(node => node.classList.contains('is-open'))), [false, false], host + ': new thinking must respect collapse');
      assert.deepEqual(await groups.locator('.work-head').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-expanded'))), ['false', 'false']);
      await groups.last().locator('.work-head').click();
      await page.evaluate(id => {
        const parent = document.getElementById(id);
        parent.querySelector('.tool').className = 'tool ok';
        const answer = document.createElement('div');
        answer.className = 'msg assistant';
        parent.appendChild(answer);
        parent.appendChild(Tomo.buildReasoningCard('Thinking after reopening'));
      }, host);
      await page.waitForFunction(id => document.querySelectorAll('#' + id + ' > .work').length === 3, host);
      assert.equal(await groups.last().locator('.work-head').getAttribute('aria-expanded'), 'true', host + ': reopening changes the preference');
    }
    await page.evaluate(() => {
      const turn = document.createElement('div');
      turn.className = 'turn';
      turn.id = 'next';
      document.querySelector('.chat-scroll').appendChild(turn);
      turn.appendChild(Tomo.buildReasoningCard('New turn'));
    });
    await page.waitForFunction(() => document.querySelector('#next > .work.is-open'));
    const resumed = await page.evaluate(async () => {
      const turn = document.createElement('div');
      turn.className = 'turn';
      document.querySelector('.chat-scroll').appendChild(turn);
      turn.appendChild(Tomo.buildReasoningCard('Historical thinking'));
      turn.appendChild(Tomo.buildToolCard({ tool: 'read_file', call_id: 'old', args: {} }));
      await new Promise(resolve => setTimeout(resolve, 0));
      const es = new EventTarget();
      const noop = () => {};
      const attachment = TomoTurnStream.attach(es, {
        mode: 'resume', wrap: document.querySelector('.chat-wrap'), turn,
        defaultAgentName: 'Tomo', agentId: 'main', esc: Tomo.escapeHtml,
        agentColor: () => '', setSending: noop, setStatus: noop,
        busyStatusLabel: () => 'Working', refreshSendBtn: noop, atBottom: noop,
      });
      const emit = (type, data) => es.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
      try {
        emit('thinking_delta', { content: 'Historical thinking' });
        emit('thinking', { content: 'Historical thinking' });
        emit('tool', { tool: 'read_file', call_id: 'old', args: {} });
        emit('thinking_delta', { content: 'Active thinking' });
        emit('caught_up', {});
        emit('thinking_delta', { content: ' continued' });
        await new Promise(resolve => setTimeout(resolve, 0));
        const notes = Array.from(turn.querySelectorAll('.si-think'), el => el.textContent);
        emit('thinking', { content: 'Active thinking continued' });
        emit('tool', { tool: 'read_file', call_id: 'new', args: {} });
        await new Promise(resolve => setTimeout(resolve, 0));
        emit('thinking_delta', { content: 'Newest thinking' });
        await new Promise(resolve => setTimeout(resolve, 0));
        const timeline = Array.from(turn.querySelectorAll('.si-think, .tool'), el =>
          el.classList.contains('tool') ? el.dataset.callId : el.textContent);
        return { notes, timeline };
      } finally { attachment.dispose(); }
    });
    assert.deepEqual(resumed.notes, ['Historical thinking', 'Active thinking continued']);
    assert.deepEqual(resumed.timeline, [
      'Historical thinking', 'old', 'Active thinking continued', 'new', 'Newest thinking',
    ]);
    assert.deepEqual(errors, []);
    console.log('Thinking collapse and resumed thinking/tool timeline ordering passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

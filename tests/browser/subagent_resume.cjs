/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/subagent_resume.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://resume.test/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><div id="sessionList"></div><div id="sessionEmpty"></div><div id="chatAgentName"></div><div id="chatSessionMeta"></div><div id="sessionChat"><div class="chat-main"><nav id="chatQueryRail" hidden></nav><div class="chat-scroll"></div></div></div>' }));
    // A fresh document models both refresh and returning from another page.
    for (let visit = 0; visit < 2; visit++) {
      await page.goto('about:blank');
      await page.goto('http://resume.test/?s=fixture');
      for (const name of ['tomo.js', 'chat_worknotes.js', 'chat_turn_stream.js']) {
        await page.addScriptTag({ path: path.join(root, 'app/static/js', name) });
      }
      await page.evaluate(() => {
        const noop = () => {};
        const entries = [
          { type: 'user', agent_id: 'main', content: 'Check the cluster' },
          { type: 'delegate', agent_id: 'ops', delegate_call_id: 'd1', params: { to: 'ops', to_name: 'Ops', task: 'First task' } },
          { type: 'thinking', agent_id: 'ops', delegate_call_id: 'd1', content: 'Before steer' },
          { type: 'tool_call', agent_id: 'ops', delegate_call_id: 'd1', call_id: 't1', function: 'bash', params: { command: 'hostname' } },
          { type: 'tool_output', agent_id: 'ops', delegate_call_id: 'd1', call_id: 't1', function: 'bash', content: 'node1' },
          { type: 'delegate', agent_id: 'ops', delegate_call_id: 'd2', params: { to: 'ops', to_name: 'Ops', task: 'Second task' } },
          { type: 'user', steered: true, steer_id: 's1', content: 'Keep production safe' },
          { type: 'thinking', agent_id: 'ops', delegate_call_id: 'd1', content: 'After steer, still worker activity' },
          { type: 'tool_call', agent_id: 'ops', delegate_call_id: 'd1', call_id: 'pending', function: 'bash', params: { command: 'check pods' } },
        ];
        Tomo.api = async url => {
          if (url === '/api/sessions') return { sessions: [{ id: 'fixture', agent_id: 'main', agent_ids: ['main'], title: 'Fixture' }], agents: [{ id: 'main', name: 'Tomo', enabled: true }, { id: 'ops', name: 'Ops', enabled: true }] };
          if (url === '/api/sessions/fixture/chat') return { entries };
          return {};
        };
        Tomo.toast = message => { throw new Error(message); };
        window.TomoChat = {
          setMarkdown: (el, text) => { el.textContent = text; },
          init: wrap => ({
            rehydratePending: async () => true,
            resume: () => {
              const listeners = {};
              window.emit = (type, data) => (listeners[type] || []).forEach(fn => fn({ data: JSON.stringify(data) }));
              const scroll = wrap.querySelector('.chat-scroll');
              window.attachment = TomoTurnStream.attach({ addEventListener: (type, fn) => (listeners[type] ||= []).push(fn) }, {
                mode: 'resume', wrap, scroll, turn: scroll.lastElementChild, agentId: 'main', defaultAgentName: 'Tomo',
                esc: Tomo.escapeHtml, agentColor: Tomo.avatarColor, currentSessionId: () => 'fixture',
                bubbleHtml: () => '<div class="msg assistant"><div class="bubble-body"></div></div>',
                setMarkdown: (el, text) => { el.textContent = text; }, atBottom: noop, setStatus: noop, busyStatusLabel: () => 'busy',
                onBindHitl: noop, refreshSendBtn: noop, closeStream: noop, finishTurn: noop, setSending: noop, getSending: () => true,
              });
              window.ready = true;
              return true;
            },
          }),
        };
      });
      await page.addScriptTag({ path: path.join(root, 'app/static/js/sessions.js') });
      await page.waitForFunction(() => window.ready);
      assert.equal(await page.locator('.swarm-row').count(), 2);
      assert.equal(await page.locator('.chat-scroll > .turn .reasoning-card').count(), 0, 'History must not leak worker reasoning into main');
      assert.equal(await page.locator('.chat-scroll > .turn .tool').count(), 0, 'History must not leak worker tools into main');
      await page.locator('.swarm-row[data-instance-key="d:d1"]').click();
      assert.match(await page.locator('.si-body').textContent(), /After steer, still worker activity/);
      assert.equal(await page.locator('.si-body .tool').count(), 2);
      await page.evaluate(() => {
        emit('delegate', { to: 'ops', agent: 'Ops', delegate_call_id: 'd1', task: 'First task' });
        emit('thinking', { agent_id: 'ops', delegate_call_id: 'd1', content: 'Before steer' });
        emit('tool', { agent_id: 'ops', delegate_call_id: 'd1', tool: 'bash', call_id: 't1', args: { command: 'hostname' } });
        emit('tool_result', { agent_id: 'ops', delegate_call_id: 'd1', tool: 'bash', call_id: 't1', result: 'node1' });
        emit('thinking', { agent_id: 'ops', delegate_call_id: 'd1', content: 'After steer, still worker activity' });
        emit('tool', { agent_id: 'ops', delegate_call_id: 'd1', tool: 'bash', call_id: 'pending', args: { command: 'check pods' } });
        emit('delta', { agent_id: 'ops', delegate_call_id: 'd1', content: 'Unfinished worker text' });
        emit('caught_up', {});
        emit('tool_result', { agent_id: 'ops', delegate_call_id: 'd1', tool: 'bash', call_id: 'pending', result: 'pods healthy' });
        emit('thinking', { agent_id: 'ops', delegate_call_id: 'd1', content: 'New worker step after reconnect' });
      });
      assert.equal(await page.locator('.swarm-row').count(), 2, 'Replay reuses the original cards');
      assert.equal(await page.locator('.si-body .tool').count(), 2, 'Replay does not duplicate tools');
      assert.match(await page.locator('.si-body').textContent(), /pods healthy/);
      assert.match(await page.locator('.si-body').textContent(), /New worker step after reconnect/);
      assert.match(await page.locator('.si-body').textContent(), /Unfinished worker text/);
      await page.evaluate(() => {
        emit('thinking_delta', { agent_id: 'ops', delegate_call_id: 'd1', content: 'Live reasoning in inspector' });
        emit('tool', { agent_id: 'ops', delegate_call_id: 'd1', tool: 'bash', call_id: 'live-output', args: { command: 'stream logs' } });
        emit('tool_output_delta', { agent_id: 'ops', delegate_call_id: 'd1', call_id: 'live-output', content: 'Live log line' });
      });
      assert.match(await page.locator('.si-body').textContent(), /Live reasoning in inspector/);
      assert.match(await page.locator('.si-body').textContent(), /Live log line/);
      await page.locator('.swarm-row[data-instance-key="d:d2"]').click();
      await page.evaluate(() => {
        emit('delta', { agent_id: 'ops', delegate_call_id: 'd2', content: 'Second independent task' });
        emit('done', { agent_id: 'ops', delegate_call_id: 'd2', content: 'Second independent task' });
        emit('subagent_done', { agent_id: 'ops', delegate_call_id: 'd2', status: 'ok' });
      });
      assert.match(await page.locator('.si-body').textContent(), /Second independent task/);
      assert.equal(await page.locator('.si-answer-body').textContent(), 'Second independent task', 'Final replaces streamed text');
      assert.doesNotMatch(await page.locator('.si-body').textContent(), /New worker step/);
      assert.equal(await page.locator('.si-status').textContent(), 'done');
      await page.evaluate(() => { emit('delta', { agent_id: 'main', content: 'Parent answer after steer' }); });
      assert.match(await page.locator('.chat-scroll .msg.assistant').textContent(), /Parent answer after steer/);
      const mainContent = await page.locator('.chat-scroll').evaluate(el => {
        const clone = el.cloneNode(true);
        clone.querySelectorAll('.swarm-card').forEach(card => card.remove());
        return clone.textContent;
      });
      assert.doesNotMatch(mainContent, /Unfinished worker text|Second independent task|New worker step after reconnect|Live reasoning in inspector|Live log line/);
      await page.evaluate(() => attachment.dispose());
      console.log(`${visit ? 'Return to chat' : 'Refresh'}: history routing, replay dedupe, live inspector and separate delegation buffers passed`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

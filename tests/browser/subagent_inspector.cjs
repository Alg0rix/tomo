/* NODE_PATH=<existing Playwright installation>/node_modules node tests/browser/subagent_inspector.cjs */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://inspector.test/', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html></html>' }));
    for (const width of [390, 820, 1280]) {
      for (const mode of ['delegate', 'swarm']) {
        await page.setViewportSize({ width, height: 844 });
        await page.goto('http://inspector.test/');
        await page.setContent('<!doctype html><html data-theme="dark"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main class="sessions-page" style="height:100dvh"><div class="sessions-main"><div class="chat-wrap sessions-chat" id="wrap"><div class="chat-main"><div class="chat-scroll" id="scroll"><div class="turn" id="turn"><p>Original coordinator conversation</p></div></div></div></div></div></main></body></html>');
        for (const css of ['tomo.css', 'chat.css']) await page.addStyleTag({ path: path.join(root, 'app/static/css', css) });
        for (const js of ['tomo.js', 'chat_worknotes.js', 'swarm_run.js', 'chat_turn_stream.js']) await page.addScriptTag({ path: path.join(root, 'app/static/js', js) });
        await page.evaluate(mode => {
          const listeners = {};
          const stream = { addEventListener: (name, fn) => (listeners[name] ||= []).push(fn), close() {} };
          const emit = (name, data) => (listeners[name] || []).forEach(fn => fn({ data: JSON.stringify(data) }));
          const noop = () => {};
          TomoTurnStream.attach(stream, {
            mode: 'live', wrap: document.getElementById('wrap'), scroll: document.getElementById('scroll'), turn: document.getElementById('turn'),
            agentId: 'main', defaultAgentName: 'Tomo', esc: Tomo.escapeHtml, agentColor: Tomo.avatarColor,
            bubbleHtml: () => '<div class="bubble"><div class="bubble-body"></div></div>',
            setMarkdown: (el, text) => { el.textContent = text; }, atBottom: noop, setStatus: noop, busyStatusLabel: () => 'busy',
            onApproval: noop, refreshSendBtn: noop, closeStream: noop, closeTransport: noop, finishTurn: noop, reconnectStream: noop,
            scheduleQueueDrain: noop, setSending: noop, getSending: () => true, messageQueue: [], sendMessage: noop,
            dispatchUiAction: noop, currentSessionId: () => 'fixture', onBindHitl: noop,
          });
          for (let i = 0; i < 2; i++) {
            const agent = { agent_id: 'worker' + i, agent: 'Worker ' + i, delegate_call_id: 'task' + i };
            const task = 'Check the cluster and return a report. ' + 'Long task context. '.repeat(30);
            if (mode === 'swarm') {
              if (!i) emit('swarm.event', { run_id: 'swarm1', event_id: 1, kind: 'run_started', coordinator_id: 'main', request: 'Inspect the cluster' });
              emit('swarm.event', { run_id: 'swarm1', event_id: 2 + i * 2, kind: 'task_created', task_id: agent.delegate_call_id, agent_id: agent.agent_id, agent_name: agent.agent, brief: task });
              emit('swarm.event', { run_id: 'swarm1', event_id: 3 + i * 2, kind: 'task_started', task_id: agent.delegate_call_id, agent_id: agent.agent_id });
            }
            emit('subagent_start', { ...agent, task });
            for (let note = 0; note < 12; note++) {
              emit('thinking', { ...agent, content: 'Working notes for step ' + note + '. '.repeat(50) });
              emit('tool', { ...agent, tool: 'bash', call_id: 'step' + i + '-' + note, args: { command: 'check step ' + note } });
              emit('tool_result', { ...agent, tool: 'bash', call_id: 'step' + i + '-' + note, result: 'Step complete.' });
            }
            emit('tool', { ...agent, tool: 'bash', call_id: 'call' + i, args: { command: 'ip addr' } });
            emit('tool_result', { ...agent, tool: 'bash', call_id: 'call' + i, result: 'address'.repeat(120) + '\n' + 'output line\n'.repeat(30) });
            emit('delta', { ...agent, content: 'Final answer. '.repeat(180) });
          }
        }, mode);
        await page.locator('.swarm-row').first().click();
        const panel = page.locator('.subagent-inspector');
        await panel.waitFor();
        await page.waitForTimeout(250); // existing inspector entrance animation
        await panel.locator('.tool-head').last().click(); // long output from the reported layout
        await panel.locator('.tool.expanded .tool-body').waitFor();
        const dimensions = await page.evaluate(() => {
          const panel = document.querySelector('.subagent-inspector');
          const body = panel.querySelector('.si-body');
          const shell = panel.parentElement.getBoundingClientRect();
          const box = panel.getBoundingClientRect();
          const close = panel.querySelector('.si-close').getBoundingClientRect();
          return { shell: { x: shell.x, y: shell.y, width: shell.width, height: shell.height }, box: { x: box.x, y: box.y, width: box.width, height: box.height }, close: { y: close.y, bottom: close.bottom }, overflow: body.scrollWidth - body.clientWidth, scrollable: body.scrollHeight > body.clientHeight };
        });
        assert(dimensions.box.x >= dimensions.shell.x && dimensions.box.x + dimensions.box.width <= dimensions.shell.x + dimensions.shell.width + 1);
        assert(dimensions.close.y >= dimensions.shell.y && dimensions.close.bottom <= dimensions.shell.y + dimensions.shell.height);
        assert(dimensions.overflow <= 1, JSON.stringify(dimensions));
        assert(dimensions.scrollable, JSON.stringify(dimensions));
        if (width <= 900) {
          assert.equal(Math.round(dimensions.box.height), Math.round(dimensions.shell.height));
          assert.equal(Math.round(dimensions.box.y), Math.round(dimensions.shell.y));
        } else {
          assert(dimensions.box.width < dimensions.shell.width / 2);
          assert(await page.locator('.chat-main').isVisible());
        }
        await panel.locator('.si-pill').last().click();
        await panel.locator('.si-name').filter({ hasText: 'Worker 1' }).waitFor();
        await panel.locator('.tool-head').last().click();
        await panel.locator('.tool.expanded .tool-body').waitFor();
        if (process.env.SCREENSHOT_DIR && width === 390) {
          fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
          await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, `subagent-${mode}-mobile.png`) });
        }
        await panel.locator('.si-close').click();
        assert.equal(await page.locator('.subagent-inspector').count(), 0);
        assert(await page.locator('.chat-main').isVisible());
        console.log(`${mode} ${width}px: panel bounds, header, content scroll, switching and close passed`);
      }
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

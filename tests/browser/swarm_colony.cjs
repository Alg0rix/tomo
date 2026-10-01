/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/swarm_colony.cjs */
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
    await page.route('http://colony.test/', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }));
    for (const width of [390, 820, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto('http://colony.test/');
      await page.setContent('<html data-theme="dark"><body><div class="chat-wrap" style="display:block;height:auto"><section class="swarm-card" id="card"></section></div></body></html>');
      for (const css of ['tomo.css', 'chat.css']) await page.addStyleTag({ path: path.join(root, 'app/static/css', css) });
      for (const js of ['tomo.js', 'swarm_run.js']) await page.addScriptTag({ path: path.join(root, 'app/static/js', js) });
      const states = await page.evaluate(() => {
        const now = Date.now() / 1000;
        const events = [
          { id: 101, kind: 'run_started', payload: { coordinator_id: 'main' } },
          { id: 102, kind: 'task_created', task_id: 'worker', payload: { agent_id: 'research', agent_name: 'API reviewer' } },
          { id: 103, kind: 'task_started', task_id: 'worker', payload: {} },
          { id: 104, kind: 'question', task_id: 'worker', payload: { agent_id: 'research', to_agent_id: 'main', content: 'Which contract?' } },
        ];
        const api = { id: 'waiting', status: 'running', created_at: now, events, tasks: [
          { id: 'worker', agent_id: 'research', status: 'running', brief: 'Check API' }
        ] };
        const card = document.getElementById('card');
        const badge = () => card.querySelector('.sw-wait');
        let run = TomoSwarm.fromApi(api, []);
        TomoSwarm.mount(card, run, { collapse: false });
        const waiting = badge().hidden ? '' : badge().textContent;
        const headline = card.querySelector('.sr-line').textContent;
        const bounds = badge().getBoundingClientRect();
        const apply = (id, kind, payload) => {
          TomoSwarm.apply(run, { run_id: 'waiting', event_id: id, kind, task_id: 'worker', ...payload });
          TomoSwarm.paint(card);
        };
        apply(105, 'message', { agent_id: 'main', reply_to_event_id: 104, content: 'Use v2' });
        const sentStillWaiting = !badge().hidden;
        apply(106, 'question_resolved', { question_event_id: 999, status: 'answered' });
        const unrelatedStillWaiting = !badge().hidden;
        apply(107, 'question_resolved', { question_event_id: 104, status: 'answered' });
        const answeredHidden = badge().hidden;
        apply(108, 'question', { content: 'Another decision?' });
        apply(109, 'question_resolved', { question_event_id: 108, status: 'timeout' });
        const timeout = badge().textContent;
        apply(110, 'question', { content: 'Retry decision?' });
        apply(111, 'run_done', { status: 'cancelled' });
        const cancelledHidden = badge().hidden;
        // Rehydrate the persisted resolved state after refresh.
        run = TomoSwarm.fromApi({ ...api, events: [...events, { id: 107, kind: 'question_resolved',
          task_id: 'worker', payload: { question_event_id: 104, status: 'answered' } }] }, []);
        TomoSwarm.mount(card, run, { collapse: false });
        return { waiting, headline, bounds: { left: bounds.left, right: bounds.right }, sentStillWaiting,
          unrelatedStillWaiting, answeredHidden, timeout, cancelledHidden, resumedHidden: badge().hidden };
      });
      assert.equal(states.waiting, 'Menunggu jawaban main');
      assert.equal(states.headline, '1 awaiting main');
      assert.ok(states.bounds.left >= 0 && states.bounds.right <= width);
      assert.ok(states.sentStillWaiting && states.unrelatedStillWaiting);
      assert.ok(states.answeredHidden && states.cancelledHidden && states.resumedHidden);
      assert.equal(states.timeout, 'Jawaban main belum diterima');
      const result = await page.evaluate(() => {
        const events = [
          { id: 1, kind: 'run_started', payload: { coordinator_id: 'main', coordinator_name: 'Tomo' } },
          { id: 2, kind: 'question', task_id: 't1', payload: { agent_id: 'research', to_agent_id: 'main', content: 'Which contract?' } },
          { id: 3, kind: 'message', payload: { agent_id: 'main', to_agent_id: 'research', content: 'Use v2' } },
          { id: 4, kind: 'message_received', task_id: 't1', payload: { agent_id: 'research', source_event_id: 3 } },
          { id: 5, kind: 'coordinator_note', payload: { agent_id: 'main', content: 'Answered the worker' } },
        ];
        const api = { id: 'run', status: 'done', events, tasks: [{ id: 't1', agent_id: 'research', status: 'done', brief: 'Check contract' }] };
        const run = TomoSwarm.fromApi(api, []);
        const card = document.getElementById('card');
        TomoSwarm.mount(card, run, { collapse: false });
        const text = card.querySelector('.sr-board').textContent;
        const delivered = card.querySelector('.sr-board em[title]');
        const before = run.board.length;
        TomoSwarm.apply(run, { run_id: 'run', event_id: 6, kind: 'coordinator_review', agent_id: 'main' });
        TomoSwarm.paint(card);
        const reviewText = card.querySelector('.sr-board header').textContent;
        TomoSwarm.apply(run, { run_id: 'run', event_id: 3, kind: 'message', agent_id: 'main', content: 'Use v2' });
        return { text, title: delivered && delivered.title, reviewText, before, after: run.board.length };
      });
      assert.match(result.text, /question/);
      assert.match(result.text, /coordination/);
      assert.match(result.text, /delivered/);
      assert.match(result.title, /action is not confirmed/);
      assert.match(result.reviewText, /Tomo reviewing/);
      assert.equal(result.before, 3);
      assert.equal(result.after, result.before);
    }
    assert.deepEqual(errors, []);
    console.log('Swarm questions, coordinator notes, delivery receipts and replay deduplication passed at 390/820/1280px');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });

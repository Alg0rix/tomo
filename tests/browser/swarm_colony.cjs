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
      await page.setContent('<html data-theme="dark"><body><section class="swarm-card" id="card"></section></body></html>');
      for (const css of ['tomo.css', 'chat.css']) await page.addStyleTag({ path: path.join(root, 'app/static/css', css) });
      for (const js of ['tomo.js', 'swarm_run.js']) await page.addScriptTag({ path: path.join(root, 'app/static/js', js) });
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

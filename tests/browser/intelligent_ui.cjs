/* NODE_PATH=/tmp/tomo-pw/node_modules node tests/browser/intelligent_ui.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-intelligent-ui-'));
  const node = {
    type: 'sandbox', title: 'Split the bill', initialHeight: 440,
    html: `<div class="splitter"><h1>Split the bill fairly</h1><p class="intro">Adjust the total, tip, and group size. Everyone’s share updates instantly.</p><div class="controls"><label>Bill total (Rp)<input id="total" type="number" min="0" value="450000"></label><label>Tip <span id="tip-label">10%</span><input id="tip" type="range" min="0" max="30" value="10"></label><label>People<input id="people" type="number" min="1" max="100" value="3"></label></div><div class="result"><span>PER PERSON</span><strong id="share"></strong><small id="summary"></small></div><button id="follow">Plan dinner for this group →</button></div>`,
    css: `.splitter{width:100%;padding:0}h1{font-size:24px;letter-spacing:-.5px;margin:0 0 12px}.intro{color:var(--color-text-secondary);line-height:1.6}.controls{display:grid;grid-template-columns:2fr 2fr 1fr;gap:24px;margin:28px 0}label{display:flex;flex-direction:column;gap:10px;font-size:12px}input{width:100%;min-width:0}input[type=range]{accent-color:#b95839;height:36px}#tip-label{float:right}.result{padding:8px 0;display:flex;flex-direction:column;gap:8px}.result span{font-size:10px;letter-spacing:2px;color:var(--color-text-secondary)}strong{font-size:38px;font-weight:500}small{color:var(--color-text-secondary)}button{margin-top:20px;padding:10px 16px;cursor:pointer}@media(max-width:500px){.controls{grid-template-columns:1fr}.splitter{padding:4px}h1{font-size:24px}}`,
    jsFunctions: `function update(){const totalInput=document.getElementById('total'),peopleInput=document.getElementById('people'),tipInput=document.getElementById('tip'),total=totalInput.valueAsNumber,people=peopleInput.valueAsNumber,tip=tipInput.valueAsNumber;const validTotal=Number.isFinite(total)&&total>=0&&total<=100000000,validPeople=Number.isInteger(people)&&people>=1&&people<=100,validTip=Number.isFinite(tip)&&tip>=0&&tip<=30;[[totalInput,validTotal],[peopleInput,validPeople],[tipInput,validTip]].forEach(([input,valid])=>input.setAttribute('aria-invalid',String(!valid)));if(!validTotal||!validPeople||!validTip){document.getElementById('share').textContent='Enter valid amounts';document.getElementById('summary').textContent='Use a nonnegative total and 1–100 people.';return;}const grandTotal=Math.round(total*(1+tip/100)),base=Math.floor(grandTotal/people),extra=grandTotal%people;document.getElementById('tip-label').textContent=tip+'%';document.getElementById('share').textContent='Rp '+base.toLocaleString('id-ID')+(extra?' – Rp '+(base+1).toLocaleString('id-ID'):'');document.getElementById('summary').textContent='Includes '+tip+'% tip · '+people+' people'+(extra?' · '+extra+' people pay Rp 1 extra to balance the total':'');}function follow(){sendPrompt({text:'Plan dinner for '+document.getElementById('people').value+' people'});}`,
    jsExpressions: `document.querySelectorAll('input').forEach(input=>input.addEventListener('input',update));document.getElementById('follow').addEventListener('click',follow);update();`,
  };
  // Go through the real tool validator before sending saved history to the page.
  const spec = JSON.parse(execFileSync(path.join(root, '.venv/bin/python'), ['-c',
    'import json,sys; from app.runtime.tools.render_ui import run; print(run(json.load(sys.stdin)))'
  ], { cwd: root, input: JSON.stringify({ ui_id: 'bill-splitter', tree: node }) }).toString());
  const html = execFileSync(path.join(root, '.venv/bin/python'), ['-c', `
from fastapi.testclient import TestClient
from app.main import app
from app.services import store
store.create_user({'username': 'ui_preview', 'password': 'preview_password1'})
client = TestClient(app)
client.post('/login', data={'username': 'ui_preview', 'password': 'preview_password1'})
response = client.get('/sessions')
assert response.status_code == 200 and 'sessionsPage' in response.text
print(response.text)
`], { cwd: root, env: { ...process.env, TOMO_HOME: home }, maxBuffer: 3 * 1024 * 1024 }).toString().replaceAll('http://testserver', '');
  const entries = [
    { message_id: 1, type: 'user', content: 'Make an interactive bill splitter for dinner with friends.', agent_id: 'main' },
    { message_id: 2, type: 'ui', params: spec, agent_id: 'main' },
    { message_id: 3, type: 'final', content: 'Here’s a bill splitter you can adjust. Change the total, tip, or number of people to see everyone’s share.', agent_id: 'main' },
    { message_id: 4, type: 'ui', params: spec, agent_id: 'main' },
  ];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://local');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith('/static/')) {
      const file = path.join(root, 'app', url.pathname);
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.png') ? 'image/png' : 'text/css');
      return res.end(fs.existsSync(file) ? fs.readFileSync(file) : '');
    }
    if (url.pathname === '/sessions') { res.setHeader('Content-Type', 'text/html'); return res.end(html); }
    if (url.pathname === '/api/sessions') return json({ sessions: [{ id: 'intelligent-ui', title: 'Dinner with friends', agent_id: 'main', agent_ids: ['main'], active_turn: false }], agents: [{ id: 'main', name: 'Tomo', enabled: true }] });
    if (url.pathname.endsWith('/chat/queries')) return json({ queries: [entries[0]] });
    if (url.pathname.endsWith('/chat')) return json({ entries, has_more: false });
    if (url.pathname.endsWith('/pending')) return json({ active_turn: false, approvals: [], clarifications: [] });
    return json({});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.connectedIframeMoves = 0;
      for (const method of ['appendChild', 'insertBefore']) {
        const original = Node.prototype[method];
        Node.prototype[method] = function (child, ...args) {
          if (child.isConnected && child.querySelector && child.querySelector('iframe')) {
            window.connectedIframeMoves++;
          }
          return original.call(this, child, ...args);
        };
      }
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/sessions?s=intelligent-ui`);
    const widget = page.frameLocator('.gen-ui-sandbox iframe');
    await widget.locator('#share').waitFor();
    assert.equal(await widget.locator('#share').textContent(), 'Rp 165.000');
    assert.equal(await page.evaluate(() => connectedIframeMoves), 0, 'History final-answer placement and duplicate UI must keep iframe nodes connected');
    assert.equal(await page.locator('.gen-ui-block').count(), 1);
    assert.equal(await page.locator('.gen-ui-block').evaluate(el => el.previousElementSibling.matches('.msg.assistant')), true);
    await widget.locator('#people').fill('5');
    assert.equal(await widget.locator('#share').textContent(), 'Rp 99.000');
    await widget.locator('#total').fill('500000');
    await widget.locator('#tip').evaluate(el => { el.value = '20'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    assert.equal(await widget.locator('#share').textContent(), 'Rp 120.000');
    await widget.locator('#people').fill('0');
    assert.equal(await widget.locator('#people').getAttribute('aria-invalid'), 'true');
    assert.equal(await widget.locator('#share').textContent(), 'Enter valid amounts');
    await widget.locator('#people').fill('3');
    await widget.locator('#total').fill('100');
    await widget.locator('#tip').evaluate(el => { el.value = '10'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    assert.equal(await widget.locator('#share').textContent(), 'Rp 36 – Rp 37');
    assert.match(await widget.locator('#summary').textContent(), /2 people pay Rp 1 extra/);
    await widget.locator('#people').fill('5');
    await widget.locator('#total').fill('450000');
    const frame = page.frames().find(frame => frame.url() === 'about:srcdoc');
    assert.deepEqual(await frame.evaluate(() => {
      const denied = fn => { try { fn(); return false; } catch (_) { return true; } };
      return { parent: denied(() => parent.document.body), storage: denied(() => localStorage.getItem('secret')) };
    }), { parent: true, storage: true });
    await page.evaluate(() => {
      const root = document.querySelector('.gen-ui');
      window.actions = [];
      TomoGenerativeUI.mount(root.parentElement, root._genSpec, { sessionId: 'intelligent-ui', dispatch: action => actions.push(action) });
    });
    // An identical update should preserve the calculator's local value.
    assert.equal(await widget.locator('#people').inputValue(), '5');
    // Explicitly mount with a fresh id to exercise the host action callback.
    await page.evaluate(spec => {
      const host = document.createElement('div'); host.id = 'bridge-test'; document.body.append(host);
      TomoGenerativeUI.mount(host, { ...spec, ui_id: 'bridge-test' }, { dispatch: action => window.actions.push(action) });
    }, spec);
    const bridgeWidget = page.frameLocator('#bridge-test iframe');
    await bridgeWidget.locator('#follow').click();
    await page.waitForFunction(() => window.actions.length === 1);
    assert.deepEqual(await page.evaluate(() => actions[0]), { ui_id: 'bridge-test', action: 'sendPrompt', payload: { text: 'Plan dinner for 3 people' } });
    await page.evaluate(() => window.postMessage({ type: 'tomo:prompt', value: 'Forged' }, '*'));
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => actions.length), 1);
    const bridgeFrame = page.frames().find(f => f !== frame && f.url() === 'about:srcdoc');
    await bridgeFrame.evaluate(() => parent.postMessage({ type: 'tomo:resize', value: 99999 }, '*'));
    await page.waitForFunction(() => document.querySelector('#bridge-test iframe').style.height === '1200px');
    await bridgeFrame.evaluate(() => { window.openLink('javascript:alert(1)'); window.sendPrompt({ text: 'x'.repeat(8001) }); });
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => actions.length), 1);
    await page.evaluate(() => { window.openedLinks = []; window.open = (...args) => openedLinks.push(args); });
    await bridgeFrame.evaluate(() => {
      openLink({ url: 'https://example.com/help' });
      openLink({ url: 'http://example.com/' });
      openLink({ url: 'https://user:secret@example.com/' });
      openLink({ url: 'javascript:alert(1)' });
    });
    await page.waitForFunction(() => openedLinks.length === 1);
    assert.deepEqual(await page.evaluate(() => openedLinks), [['https://example.com/help', '_blank', 'noopener,noreferrer']]);
    assert.equal(await bridgeFrame.evaluate(async () => {
      try { await fetch('/api/sessions'); return false; } catch (_) { return true; }
    }), true);
    await page.evaluate(() => {
      const source = document.querySelector('#bridge-test iframe').contentWindow;
      document.getElementById('bridge-test').remove();
      window.dispatchEvent(new MessageEvent('message', { source, data: { type: 'tomo:prompt', value: 'Removed widget' } }));
    });
    assert.equal(await page.evaluate(() => actions.length), 1);
    await page.reload();
    await widget.locator('#share').waitFor();
    assert.equal(await widget.locator('#share').textContent(), 'Rp 165.000');
    await page.waitForFunction(() => parseInt(document.querySelector('.gen-ui-sandbox iframe').style.height) > 300);
    assert.equal(await page.locator('.gen-ui-block-hd').count(), 0);
    for (const selector of ['.gen-ui-block', '.gen-ui-sandbox iframe']) {
      assert.deepEqual(await page.locator(selector).evaluate(el => {
        const style = getComputedStyle(el);
        return { border: style.borderTopWidth, background: style.backgroundColor, shadow: style.boxShadow };
      }), { border: '0px', background: 'rgba(0, 0, 0, 0)', shadow: 'none' });
    }
    assert.equal(await widget.locator('.result').evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
    const screenshot = path.join(root, 'tmp/intelligent-ui-natural-desktop-v2.png');
    await page.screenshot({ path: screenshot, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await widget.locator('#share').waitFor();
    assert.equal(await widget.locator('#share').textContent(), 'Rp 165.000');
    await page.waitForFunction(() => parseInt(document.querySelector('.gen-ui-sandbox iframe').style.height) > 450);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(root, 'tmp/intelligent-ui-natural-mobile-v2.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS: calculations, opaque origin, history replay, duplicate delivery, follow-ups, source validation, resize bounds, mobile layout');
    console.log(`Screenshot: ${screenshot}`);
  } finally {
    await browser.close(); server.close(); fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

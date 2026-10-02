/* NODE_PATH=<Playwright installation>/node_modules node tests/browser/background_jobs.cjs
 * Uses real local subprocesses and a disposable Tomo home. No model API needed.
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'tomo-jobs-browser-'));
  const artifacts = process.env.TOMO_BROWSER_ARTIFACTS || process.env.BB_THREAD_STORAGE || home;
  fs.mkdirSync(artifacts, { recursive: true });
  const python = `
import json, socket
import uvicorn
from app.main import app
from app.services import store
from app.services.background_jobs import manager
from app.runtime.artifacts.fs import bind_session, reset_session
from app.runtime.tools.sandbox import bind_agent, reset_agent
from app.runtime.tools.user_ctx import bind_user, reset_user
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
user = store.create_user({'username': 'job_preview', 'password': 'preview_password1'})
sids = [store.create_swarm_session(['main'], user_id=user['id']) for _ in range(2)]
for sid, title in zip(sids, ['Background builds', 'Separate chat']):
    store.set_session_title(sid, title)
    store.append_session_history(sid, {'type': 'user', 'content': title})
@app.post('/__browser/start')
async def seed_jobs():
    # Only the UI is under test; continuation has separate integration coverage.
    manager._callback = None
    store.set_background_jobs_paused(sids[0], True)
    tokens = (bind_session(sids[0]), bind_agent('main'), bind_user(user['id']))
    jobs = []
    try:
        commands = ["printf '<script>literal output</script>\\n'; sleep 600", "printf 'sibling alive\\n'; sleep 600", "sleep 4; printf 'build done\\n'"]
        for command in commands:
            store.append_session_history(sids[0], {'type': 'tool_call', 'agent_id': 'main', 'function': 'bash', 'params': {'command': command, 'background': True}})
            job = manager.start(command)
            store.append_session_history(sids[0], {'type': 'tool_output', 'agent_id': 'main', 'function': 'bash', 'content': json.dumps({'job_id': job['id']})})
            jobs.append(job)
        store.append_session_history(sids[0], {'type': 'final', 'agent_id': 'main', 'content': 'Background jobs are running; agent turn finished.'})
        unknown = store.create_background_job({'session_id': sids[0], 'user_id': user['id'], 'command': 'disconnected remote build', 'backend': 'tunnel', 'status': 'unknown', 'truncated': True})
        jobs.append(unknown)
    finally:
        reset_session(tokens[0]); reset_agent(tokens[1]); reset_user(tokens[2])
    return {'jobs': jobs}
@app.post('/__browser/outcome/{job_id}/{status}')
async def set_outcome(job_id: str, status: str):
    return store.update_background_job(job_id, {'continuation_status': 'consumed', 'delivery_status': status})
@app.post('/__browser/continuation')
async def enable_continuation():
    import asyncio
    from app.runtime.agent import loop
    from app.runtime.llm.base import LLMResponse, ToolCall
    from app.services.background_continuation import on_job_update
    from app.channels import web
    class PreviewLLM:
        stage = 0
        async def complete(self, messages, tools=None):
            stage = self.stage
            self.stage += 1
            if stage == 0:
                return LLMResponse(content=None, tool_calls=[ToolCall(id='draft-build', name='bash', arguments={'command': 'sleep 3; echo browser-build-done', 'background': True})])
            if stage == 1:
                return LLMResponse(content='Browser build started.', tool_calls=[])
            if stage == 2:
                await asyncio.sleep(8)
                job = next(j for j in store.list_background_jobs() if j['command'] == 'sleep 3; echo browser-build-done')
                return LLMResponse(content=None, tool_calls=[ToolCall(id='completion-status', name='process', arguments={'action': 'status', 'id': job['id']})])
            return LLMResponse(content='Browser continuation passed.', tool_calls=[])
    preview = PreviewLLM()
    loop.get_llm = lambda agent_id=None: preview
    async def no_title(*args, **kwargs):
        return None
    web.generate_session_title = no_title
    manager._callback = on_job_update
    store.update_settings({'approvals_mode': 'auto', 'learning_enabled': False})
    return {'ready': True}
print(json.dumps({'port': sock.getsockname()[1], 'sessions': sids}), flush=True)
uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False)).run(sockets=[sock])
`;
  const child = spawn(process.env.TOMO_TEST_PYTHON || path.join(root, '.venv/bin/python'), ['-c', python], {
    cwd: root,
    env: { ...process.env, TOMO_HOME: home, TOMO_DB_PATH: path.join(home, 'state/test.db'),
      TOMO_WORK: path.join(home, 'work'), TOMO_SKILLS_EXTERNAL_DIRS: '', TOMO_SECRET_KEY: '',
      TOMO_ADMIN_PASSWORD: 'synthetic-browser-admin-password' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let logs = '', browser, page;
  child.stderr.on('data', chunk => { logs = (logs + chunk).slice(-16000); });
  try {
    const meta = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Server startup timed out\n' + logs)), 20000);
      readline.createInterface({ input: child.stdout }).on('line', line => {
        try { const value = JSON.parse(line); if (value.port) { clearTimeout(timer); resolve(value); } } catch (_) {}
      });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Server exited: ' + code + '\n' + logs)); });
    });
    const base = 'http://127.0.0.1:' + meta.port;
    const deadline = Date.now() + 20000;
    while (true) {
      try { if ((await fetch(base + '/login', { signal: AbortSignal.timeout(1000) })).ok) break; } catch (_) {}
      if (Date.now() >= deadline || child.exitCode !== null) throw new Error('Server did not become ready\n' + logs);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
    page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/login');
    await page.locator('input[name=username]').fill('job_preview');
    await page.locator('input[name=password]').fill('preview_password1');
    await page.locator('button[type=submit]').click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'));
    const jobs = await page.evaluate(async () => (await (await fetch('/__browser/start', { method: 'POST' })).json()).jobs);
    const sids = meta.sessions;
    await page.goto(base + '/sessions?s=' + sids[0]);
    await page.waitForFunction(() => document.querySelectorAll('.background-job-card').length === 4);
    assert.equal(await page.locator('.composer.is-generating').count(), 0);
    assert.equal(await page.locator('script').filter({ hasText: 'literal output' }).count(), 0);
    await page.locator('.cap-expand-strip').click();
    await page.locator('[data-workspace-view=processes]').click();
    await page.locator('[data-process-select="' + jobs[0].id + '"]').click();
    await page.waitForFunction(() => document.querySelector('.process-log')?.textContent.includes('<script>literal output</script>'));
    assert.equal(await page.locator('.process-log script').count(), 0);
    assert.match(await page.locator('[data-process-detail] .process-outcome').textContent(), /Agent idle/);
    await page.screenshot({ path: path.join(artifacts, 'background-jobs-desktop.png') });

    // Process finish stays observed after the initial agent turn and SSE close.
    await page.waitForFunction(id => Array.from(document.querySelectorAll('.background-job-card')).find(card => card.dataset.jobId === id)?.dataset.status === 'succeeded', jobs[2].id);
    const completed = page.locator('.background-job-card[data-job-id="' + jobs[2].id + '"]');
    assert.match(await completed.textContent(), /exit 0/);
    assert.match(await completed.textContent(), /Result waiting for agent/);
    await page.evaluate(async id => { await fetch('/__browser/outcome/' + id + '/pending', { method: 'POST' }); }, jobs[2].id);
    await page.waitForFunction(id => Array.from(document.querySelectorAll('.background-job-card')).find(card => card.dataset.jobId === id)?.textContent.includes('Delivery: pending'), jobs[2].id);
    await page.evaluate(async id => { await fetch('/__browser/outcome/' + id + '/sent', { method: 'POST' }); }, jobs[2].id);
    await page.waitForFunction(id => !Array.from(document.querySelectorAll('.background-job-card')).find(card => card.dataset.jobId === id)?.textContent.includes('Delivery: pending'), jobs[2].id);

    // Stop only the named job; a cancelled confirmation has no mutation.
    let confirmed = '';
    page.once('dialog', dialog => { confirmed = dialog.message(); dialog.dismiss(); });
    await page.locator('[data-process-detail]').getByRole('button', { name: 'Stop process' }).click();
    assert.match(confirmed, new RegExp(jobs[0].id));
    assert.match(confirmed, /sleep 600/);
    let snapshot = await page.evaluate(async sid => (await (await fetch('/api/sessions/' + sid + '/processes')).json()).jobs, sids[0]);
    assert.equal(snapshot.find(job => job.id === jobs[0].id).status, 'running');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('[data-process-detail]').getByRole('button', { name: 'Stop process' }).click();
    await page.waitForFunction(id => Array.from(document.querySelectorAll('.background-job-card')).find(card => card.dataset.jobId === id)?.dataset.status === 'stopped', jobs[0].id);
    snapshot = await page.evaluate(async sid => (await (await fetch('/api/sessions/' + sid + '/processes')).json()).jobs, sids[0]);
    assert.equal(snapshot.find(job => job.id === jobs[1].id).status, 'running');

    // Unknown closes monitoring without claiming remote success or stopping it.
    await page.locator('[data-process-select="' + jobs[3].id + '"]').click();
    assert.match(await page.locator('[data-process-log-note]').textContent(), /truncated/);
    await page.locator('[data-process-detail]').getByRole('link', { name: 'Go to original chat' }).click();
    assert.match(await page.locator('[data-process-origin-note]').textContent(), /removed or is unavailable/);
    page.once('dialog', dialog => { assert.match(dialog.message(), /may still be running/); dialog.accept(); });
    await page.getByRole('button', { name: 'Close monitoring', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-process-detail] .process-status')?.textContent.includes('Unknown') && document.querySelector('[data-process-detail] .process-status')?.textContent.includes('Monitoring closed'));

    // Origin is an actual history anchor, preserved across page reloads.
    await page.locator('[data-process-select="' + jobs[0].id + '"]').click();
    await page.locator('[data-process-detail]').getByRole('link', { name: 'Go to original chat' }).click();
    assert.match(page.url(), /#message-/);
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('.background-job-card').length === 4);
    assert.equal(await page.locator('.background-job-card[data-job-id="' + jobs[0].id + '"]').count(), 1);
    await page.locator('.cap-expand-strip').click();
    await page.locator('[data-workspace-view=processes]').click();
    await page.locator('.session-item[data-id="' + sids[1] + '"]').click();
    // New sessions default to Files, while remembered Processes is restored on return.
    await page.locator('[data-workspace-view=processes]').click();
    await page.waitForFunction(() => document.querySelector('[data-process-list]')?.textContent.includes('No background processes'));
    assert.equal(await page.locator('.background-job-card').count(), 0);
    await page.locator('.session-item[data-id="' + sids[0] + '"]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-process-select]').length === 4);

    // Stale fetch is honest and cannot overwrite a new chat after switching.
    const processURL = base + '/api/sessions/' + sids[0] + '/processes';
    await page.route(processURL, route => route.fulfill({ status: 503, body: 'Unavailable' }));
    await page.locator('.process-toolbar').getByRole('button', { name: 'Refresh' }).click();
    await page.waitForFunction(() => document.querySelector('[data-process-stale]')?.textContent.includes('Connection stale'));
    await page.unroute(processURL);
    await page.locator('.process-toolbar').getByRole('button', { name: 'Refresh' }).click();
    await page.waitForFunction(() => !document.querySelector('[data-process-stale]')?.textContent.includes('Connection stale'));
    await page.locator('[data-workspace-view=home]').click();
    await page.locator('[data-workspace-view=terminal]').click();
    await page.getByRole('button', { name: 'Open terminal', exact: true }).waitFor();
    await page.locator('[data-workspace-view=processes]').click();
    await page.locator('[data-process-select="' + jobs[1].id + '"]').click();
    await page.setViewportSize({ width: 430, height: 850 });
    if (await page.locator('html').evaluate(el => el.classList.contains('is-rail-open'))) await page.locator('#navToggle').click();
    await page.locator('.process-log').waitFor();
    await page.screenshot({ path: path.join(artifacts, 'background-jobs-mobile.png') });
    await page.evaluate(() => Tomo.applyTheme('light'));
    await page.screenshot({ path: path.join(artifacts, 'background-jobs-light.png') });
    await page.setViewportSize({ width: 1440, height: 950 });
    await page.evaluate(async () => { await fetch('/__browser/continuation', { method: 'POST' }); });
    await page.locator('#newChatBtn').click();
    await page.locator('#newChatAgents input[value="main"]').check();
    await page.locator('#newChatConfirm').click();
    await page.locator('.chat-input').fill('Run browser background build');
    await page.locator('.chat-send').click();
    await page.waitForFunction(() => document.querySelectorAll('.background-job-card').length === 1);
    await page.waitForFunction(() => document.querySelector('.chat-scroll')?.textContent.includes('Browser build started.'));
    await page.waitForFunction(() => document.querySelector('.background-job-marker') && document.querySelector('.composer.is-generating'));
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.chat-scroll')?.textContent.includes('Browser continuation passed.'));
    assert.equal(await page.locator('.background-job-marker').count(), 1);
    assert.equal(await page.locator('.tool[data-tool-name="process"]').count(), 1);
    assert.equal(await page.locator('.background-job-card').count(), 1);
    await page.screenshot({ path: path.join(artifacts, 'background-jobs-continuation.png') });
    assert.deepEqual(errors, []);
    console.log('PASS: real jobs after agent idle, snapshots, escaped logs, isolated stop, Unknown monitoring, anchors, reload, switch, stale recovery, Files/Terminal, mobile/light, draft-session job cards, autonomous continuation, replay without duplicate markers');
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(artifacts, 'background-jobs-failure.png') }).catch(() => {});
    throw error;
  } finally {
    if (browser) await browser.close();
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      await new Promise(resolve => {
        const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
        child.kill('SIGTERM');
      });
    }
    if (path.resolve(artifacts) !== path.resolve(home)) fs.rmSync(home, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../app/static/js/self_update.js'), 'utf8');

const installed = {
  instance_id: 'old-service', can_update: true, branch: 'main', version: '0.3.3', head: 'old', updating: false,
  commits_behind: null,
};

function mount({ api, poll, confirm = true }) {
  const ids = ['selfUpdateField', 'selfUpdateBtn', 'selfUpdateCheckBtn', 'selfUpdateStatus',
    'selfUpdateHint', 'selfUpdateDetails', 'tomoVersion'];
  const elements = Object.fromEntries(ids.map(id => [id, {
    textContent: '', disabled: false, hidden: false, attributes: {}, handlers: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    addEventListener(k, fn) { this.handlers[k] = fn; },
  }]));
  const calls = [];
  const toasts = [];
  let time = 0;
  let reloads = 0;
  const context = {
    document: { getElementById: id => elements[id] },
    window: { confirm: () => confirm, location: { reload() { reloads++; } } },
    Tomo: {
      async api(url, opts) { calls.push([url, opts]); return api(url, opts); },
      toast(...args) { toasts.push(args); },
    },
    Date: { now: () => time },
    AbortController,
    setTimeout(fn, ms) {
      if (ms === 1500) { time += ms; queueMicrotask(fn); }
      return 1;
    },
    clearTimeout() {},
    async fetch() {
      const value = await poll();
      return { status: 200, ok: true, json: async () => value };
    },
  };
  vm.runInNewContext(source, context);
  return {
    elements, calls, toasts,
    reloads: () => reloads,
    click: id => elements[id].handlers.click(),
  };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

function available(url) {
  return { ...installed, commits_behind: url.endsWith('/check') ? 2 : null, remote_head: 'new' };
}

test('available update shows branch, heads, and manual check controls', async () => {
  const ui = mount({ api: available });
  await settle();
  assert.equal(ui.elements.selfUpdateBtn.disabled, false);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
  assert.match(ui.elements.selfUpdateStatus.textContent, /2 new commits/);
  assert.match(ui.elements.selfUpdateDetails.textContent, /main.*old.*new/);
  await ui.click('selfUpdateCheckBtn');
  assert.equal(ui.calls.length, 4);
});

test('up-to-date installs disable update but allow a fresh check', async () => {
  const ui = mount({ api: () => ({ ...installed, commits_behind: 0 }) });
  await settle();
  assert.equal(ui.elements.selfUpdateBtn.disabled, true);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
  assert.match(ui.elements.selfUpdateStatus.textContent, /up to date/);
});

for (const reason of ['container', 'not_script_install']) {
  test(`${reason} installs show guidance without fetching remote`, async () => {
    const ui = mount({ api: () => ({ ...installed, can_update: false, reason }) });
    await settle();
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.elements.selfUpdateBtn.hidden, true);
    assert.equal(ui.elements.selfUpdateCheckBtn.hidden, true);
    assert.match(ui.elements.selfUpdateHint.textContent, reason === 'container' ? /image/ : /terminal/);
  });
}

test('check failure is visible and can be retried', async () => {
  let failing = true;
  const ui = mount({ api: url => {
    if (url.endsWith('/check') && failing) throw new Error('Network error');
    return available(url);
  } });
  await settle();
  assert.match(ui.elements.selfUpdateStatus.textContent, /Network error/);
  assert.equal(ui.elements.selfUpdateBtn.disabled, true);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
  failing = false;
  await ui.click('selfUpdateCheckBtn');
  assert.equal(ui.elements.selfUpdateBtn.disabled, false);
});

test('canceling confirmation never starts an update', async () => {
  const ui = mount({ api: available, confirm: false });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.elements.selfUpdateBtn.disabled, false);
});

test('slow install and changed HEAD do not imply completion before restart', async () => {
  let updating = false;
  let polls = 0;
  const ui = mount({
    api: (url, opts) => {
      if (url === '/api/update' && opts?.method === 'POST') {
        updating = true;
        return { started: true, updating: true };
      }
      if (updating) return { ...installed, head: 'new', commits_behind: 0 };
      return available(url);
    },
    poll: () => {
      polls++;
      if (polls === 15) throw new Error('Restart connection lost');
      return { ...installed, head: 'new', instance_id: polls < 20 ? 'old-service' : 'new-service', updating: polls < 20 };
    },
  });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.equal(polls, 20);
  assert.equal(ui.reloads(), 1);
  assert.equal(ui.calls.filter(([url]) => url.endsWith('/check')).length, 2);
  assert.deepEqual(ui.toasts.at(-1), ['Tomo updated successfully', 'ok']);
});

test('reopening settings during an update resumes monitoring and preserves lock on timeout', async () => {
  const ui = mount({
    api: () => ({ ...installed, updating: true }),
    poll: () => ({ ...installed, updating: true }),
  });
  await settle();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.reloads(), 0);
  assert.match(ui.elements.selfUpdateStatus.textContent, /longer than expected/);
  assert.equal(ui.elements.selfUpdateBtn.disabled, true);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
});

test('remaining commits after restart show failure and allow retry', async () => {
  const ui = mount({ api: available, poll: () => ({ ...installed, instance_id: 'new-service' }) });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.match(ui.elements.selfUpdateStatus.textContent, /did not finish/);
  assert.equal(ui.reloads(), 0);
  assert.equal(ui.elements.selfUpdateBtn.disabled, false);
});

test('failed update request shows the server error and permits rechecking', async () => {
  const ui = mount({ api: (url, opts) => {
    if (url === '/api/update' && opts?.method === 'POST') throw new Error('could not start update');
    return available(url);
  } });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.match(ui.elements.selfUpdateStatus.textContent, /could not start update/);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
  await ui.click('selfUpdateCheckBtn');
  assert.equal(ui.elements.selfUpdateBtn.disabled, false);
});


test('expired lock on the same service does not claim a successful restart', async () => {
  const ui = mount({ api: available, poll: () => ({ ...installed, head: 'new' }) });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.equal(ui.reloads(), 0);
  assert.match(ui.elements.selfUpdateStatus.textContent, /without restarting/);
});

test('lost update response checks server state and blocks a duplicate update', async () => {
  let started = false;
  const ui = mount({ api: (url, opts) => {
    if (url === '/api/update' && opts?.method === 'POST') {
      started = true;
      throw new Error('Connection lost');
    }
    return started ? { ...installed, updating: true } : available(url);
  } });
  await settle();
  await ui.click('selfUpdateBtn');
  assert.equal(ui.elements.selfUpdateBtn.disabled, true);
  assert.equal(ui.elements.selfUpdateCheckBtn.disabled, false);
  assert.match(ui.elements.selfUpdateStatus.textContent, /Connection lost/);
});

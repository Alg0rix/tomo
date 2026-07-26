/* system.js — settings SPA hash navigation + save handlers */
(function () {
  "use strict";

  var nav = document.getElementById('systemNav');
  if (!nav) return;

  function show(section) {
    document.querySelectorAll('.sys-section').forEach(function (s) { s.style.display = 'none'; });
    var el = document.getElementById('sec-' + section);
    if (el) el.style.display = 'block';
    nav.querySelectorAll('a').forEach(function (a) {
      a.classList.toggle('active', a.dataset.section === section);
    });
  }

  function fromHash() {
    var h = (location.hash || '#general').replace('#', '');
    show(h in { general: 1, models: 1, memory: 1, tools: 1, plugins: 1, hmads: 1, users: 1, shared_channel: 1, logs: 1 } ? h : 'general');
  }

  nav.querySelectorAll('a').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      location.hash = a.dataset.section;
    });
  });
  window.addEventListener('hashchange', fromHash);
  fromHash();

  var saveBtn = document.getElementById('saveGeneral');
  if (saveBtn) {
    saveBtn.addEventListener('click', async function () {
      try {
        var data = await Tomo.api('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            max_tool_iterations: parseInt(document.getElementById('setMaxIter').value, 10),
            learning_enabled: document.getElementById('setLearning').checked,
          }),
        });
        if (!data) return;
        if (data.max_tool_iterations != null) {
          document.getElementById('setMaxIter').value = data.max_tool_iterations;
        }
        Tomo.toast('Settings saved', 'ok');
      } catch (e) {
        Tomo.toast((e && e.message) || 'Could not save', 'err');
      }
    });
  }

  var saveTg = document.getElementById('saveTelegram');
  if (saveTg) {
    saveTg.addEventListener('click', async function () {
      try {
        var tokenEl = document.getElementById('setTgToken');
        var enabledEl = document.getElementById('setTgEnabled');
        var body = {
          telegram_enabled: !!(enabledEl && enabledEl.checked),
        };
        var token = tokenEl ? tokenEl.value : '';
        if (token && token.indexOf('•') === -1) body.telegram_bot_token = token;
        var data = await Tomo.api('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!data) return;
        if (tokenEl) {
          tokenEl.value = data.telegram_bot_token || '';
          tokenEl.placeholder = data.telegram_bot_token_set
            ? '•••• set — blank keeps existing'
            : 'Paste bot token from @BotFather';
        }
        Tomo.toast('Telegram settings saved', 'ok');
      } catch (e) {
        Tomo.toast((e && e.message) || 'Could not save', 'err');
      }
    });
  }

  // ---- LLM profiles (System → Models) ----
  var listEl = document.getElementById('profileList');
  var formCard = document.getElementById('profileFormCard');
  var fMode = document.getElementById('profFormMode');
  var fId = document.getElementById('profId');
  var fName = document.getElementById('profName');
  var fBase = document.getElementById('profBaseUrl');
  var fKey = document.getElementById('profApiKey');
  var fModel = document.getElementById('profModel');
  var fEnabled = document.getElementById('profEnabled');
  var defaultId = '';

  function esc(s) { return Tomo.escapeHtml(s); }

  function rowHtml(p) {
    var b = '';
    if (p.id === defaultId) b += ' <span class="badge accent sm">default</span>';
    b += p.enabled ? (p.api_key_set ? ' <span class="badge ok sm">key set</span>' : ' <span class="badge muted">no key</span>') : ' <span class="badge muted">disabled</span>';
    var def = p.id === defaultId ? '' : ' <button class="btn ghost sm" type="button" data-act="default">Set default</button>';
    return '<div class="row" data-id="' + esc(p.id) + '"><div class="meta"><div class="title">' + esc(p.name) + ' <span class="faint mono">' + esc(p.id) + '</span></div><div class="desc">' + esc(p.model || '—') + ' · ' + esc(p.base_url || 'default host') + '</div></div><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' + b + ' <button class="btn ghost sm" type="button" data-act="edit">Edit</button>' + def + ' <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function render(profiles, dId) {
    defaultId = dId || '';
    if (!listEl) return;
    if (!profiles.length) { listEl.innerHTML = '<div class="empty">No profiles yet — add one to enable chat.</div>'; return; }
    listEl.innerHTML = profiles.map(rowHtml).join('');
  }

  async function loadProfiles() {
    try {
      var d = await Tomo.api('/api/llm-profiles');
      if (d) render(d.profiles || [], d.default_id || '');
    } catch (e) { if (listEl) listEl.innerHTML = '<div class="empty">Could not load profiles.</div>'; }
  }

  function openForm(mode, p) {
    fMode.value = mode;
    document.getElementById('profileFormTitle').textContent = mode === 'add' ? 'Add profile' : 'Edit profile';
    if (mode === 'add') {
      fId.value = ''; fId.disabled = false; fName.value = ''; fBase.value = ''; fKey.value = ''; fModel.value = ''; fEnabled.checked = true;
    } else {
      fId.value = p.id; fId.disabled = true; fName.value = p.name || ''; fBase.value = p.base_url || ''; fKey.value = ''; fModel.value = p.model || ''; fEnabled.checked = !!p.enabled;
    }
    formCard.classList.remove('hidden');
  }

  var addBtn = document.getElementById('addProfileBtn');
  if (addBtn) addBtn.addEventListener('click', function () { openForm('add'); });
  var cancelProf = document.getElementById('profCancel');
  if (cancelProf) cancelProf.addEventListener('click', function () { formCard.classList.add('hidden'); });

  if (listEl) {
    listEl.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]'); if (!btn) return;
      var row = btn.closest('[data-id]'); var pid = row ? row.dataset.id : ''; var act = btn.dataset.act;
      if (act === 'edit') {
        try { var p = await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid)); if (p) openForm('edit', p); } catch (er) { Tomo.toast('Could not load profile', 'err'); }
      } else if (act === 'default') {
        try { await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid) + '/default', { method: 'POST' }); Tomo.toast('Default set', 'ok'); loadProfiles(); } catch (er) { Tomo.toast((er && er.message) || 'Could not set default', 'err'); }
      } else if (act === 'delete') {
        if (!confirm('Delete profile "' + pid + '"?')) return;
        try { await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid), { method: 'DELETE' }); Tomo.toast('Profile deleted', 'ok'); loadProfiles(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
      }
    });
  }

  var saveProf = document.getElementById('profSave');
  if (saveProf) {
    saveProf.addEventListener('click', async function () {
      var body = { name: fName.value.trim(), base_url: fBase.value.trim(), model: fModel.value.trim(), enabled: fEnabled.checked };
      var key = fKey.value;
      if (key && key.indexOf('•') === -1) body.api_key = key;
      try {
        if (fMode.value === 'add') {
          body.id = fId.value.trim();
          if (!body.id) { Tomo.toast('ID is required', 'err'); return; }
          await Tomo.api('/api/llm-profiles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          Tomo.toast('Profile created', 'ok');
        } else {
          await Tomo.api('/api/llm-profiles/' + encodeURIComponent(fId.value), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          Tomo.toast('Profile saved', 'ok');
        }
        formCard.classList.add('hidden'); loadProfiles();
      } catch (e) { Tomo.toast((e && e.message) || 'Could not save profile', 'err'); }
    });
  }

  loadProfiles();

  // ---- Knowledge entries (System → Memory) ----
  var kbList = document.getElementById('knowledgeList');
  var kbFormCard = document.getElementById('knowledgeFormCard');
  var kbMode = document.getElementById('kbFormMode');
  var kbId = document.getElementById('kbId');
  var kbTitle = document.getElementById('kbTitle');
  var kbBody = document.getElementById('kbBody');
  var kbTags = document.getElementById('kbTags');

  function parseTags(s) {
    return String(s || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean);
  }

  function kbRowHtml(e) {
    var tags = (e.tags || []).map(function (t) { return '<span class="badge muted sm">' + esc(t) + '</span>'; }).join(' ');
    var preview = (e.body || '').slice(0, 120);
    if ((e.body || '').length > 120) preview += '…';
    return '<div class="row" data-id="' + esc(e.id) + '"><div class="meta"><div class="title">' + esc(e.title) + ' <span class="faint mono">' + esc(e.id) + '</span></div><div class="desc">' + esc(preview) + (tags ? ' · ' + tags : '') + '</div></div><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><button class="btn ghost sm" type="button" data-act="edit">Edit</button> <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function renderKnowledge(entries) {
    if (!kbList) return;
    if (!entries.length) { kbList.innerHTML = '<div class="empty">No knowledge entries yet.</div>'; return; }
    kbList.innerHTML = entries.map(kbRowHtml).join('');
  }

  async function loadKnowledge() {
    try {
      var d = await Tomo.api('/api/knowledge');
      if (d) renderKnowledge(d.entries || []);
    } catch (e) { if (kbList) kbList.innerHTML = '<div class="empty">Could not load knowledge entries.</div>'; }
  }

  function openKbForm(mode, e) {
    kbMode.value = mode;
    document.getElementById('knowledgeFormTitle').textContent = mode === 'add' ? 'Add entry' : 'Edit entry';
    if (mode === 'add') {
      kbId.value = ''; kbId.disabled = false; kbTitle.value = ''; kbBody.value = ''; kbTags.value = '';
    } else {
      kbId.value = e.id; kbId.disabled = true; kbTitle.value = e.title || ''; kbBody.value = e.body || ''; kbTags.value = (e.tags || []).join(', ');
    }
    kbFormCard.classList.remove('hidden');
  }

  var addKb = document.getElementById('addKnowledgeBtn');
  if (addKb) addKb.addEventListener('click', function () { openKbForm('add'); });
  var cancelKb = document.getElementById('kbCancel');
  if (cancelKb) cancelKb.addEventListener('click', function () { kbFormCard.classList.add('hidden'); });

  if (kbList) {
    kbList.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]'); if (!btn) return;
      var row = btn.closest('[data-id]'); var eid = row ? row.dataset.id : ''; var act = btn.dataset.act;
      if (act === 'edit') {
        try { var ent = await Tomo.api('/api/knowledge/' + encodeURIComponent(eid)); if (ent) openKbForm('edit', ent); } catch (er) { Tomo.toast('Could not load entry', 'err'); }
      } else if (act === 'delete') {
        if (!confirm('Delete knowledge entry "' + eid + '"?')) return;
        try { await Tomo.api('/api/knowledge/' + encodeURIComponent(eid), { method: 'DELETE' }); Tomo.toast('Entry deleted', 'ok'); loadKnowledge(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
      }
    });
  }

  var saveKb = document.getElementById('kbSave');
  if (saveKb) {
    saveKb.addEventListener('click', async function () {
      var title = kbTitle.value.trim();
      if (!title) { Tomo.toast('Title is required', 'err'); return; }
      var body = { title: title, body: kbBody.value, tags: parseTags(kbTags.value) };
      try {
        if (kbMode.value === 'add') {
          var id = kbId.value.trim();
          if (id) body.id = id;
          await Tomo.api('/api/knowledge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          Tomo.toast('Entry created', 'ok');
        } else {
          await Tomo.api('/api/knowledge/' + encodeURIComponent(kbId.value), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
          Tomo.toast('Entry saved', 'ok');
        }
        kbFormCard.classList.add('hidden'); loadKnowledge();
      } catch (err) { Tomo.toast((err && err.message) || 'Could not save entry', 'err'); }
    });
  }

  loadKnowledge();
})();

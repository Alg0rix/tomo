/* system.js — settings SPA hash navigation + save handlers */
(function () {
  "use strict";

  var nav = document.getElementById('systemNav');
  if (!nav) return;

  var bayKicker = document.getElementById('machineBayKicker');
  var bayTitle = document.getElementById('machineBayTitle');
  var bayLede = document.getElementById('machineBayLede');
  var bay = document.getElementById('machineBay');
  var userPicked = false;
  var docks = document.querySelectorAll('.machine-dock');
  var dockBack = document.getElementById('machineDockBack');

  function closeDocks() {
    docks.forEach(function (d) { d.classList.add('hidden'); });
    document.querySelectorAll('.machine-ledger .row.is-selected').forEach(function (r) {
      r.classList.remove('is-selected');
    });
  }

  function syncDockBack() {
    var open = false;
    docks.forEach(function (d) {
      if (!d.classList.contains('hidden')) open = true;
    });
    if (dockBack) {
      dockBack.hidden = !open;
      dockBack.setAttribute('aria-hidden', open ? 'false' : 'true');
    }
    document.documentElement.classList.toggle('is-machine-dock-open', open);
    if (!open) {
      document.querySelectorAll('.machine-ledger .row.is-selected').forEach(function (r) {
        r.classList.remove('is-selected');
      });
    }
  }

  docks.forEach(function (d) {
    new MutationObserver(syncDockBack).observe(d, { attributes: true, attributeFilter: ['class'] });
  });
  if (dockBack) dockBack.addEventListener('click', closeDocks);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var any = false;
    docks.forEach(function (d) { if (!d.classList.contains('hidden')) any = true; });
    if (!any) return;
    e.preventDefault();
    closeDocks();
  });
  document.querySelectorAll('[data-dock-close]').forEach(function (btn) {
    btn.addEventListener('click', closeDocks);
  });
  syncDockBack();

  function markSelected(list, id) {
    if (!list) return;
    list.querySelectorAll('[data-id]').forEach(function (row) {
      row.classList.toggle('is-selected', !!id && row.dataset.id === id);
    });
  }

  function show(section, opts) {
    opts = opts || {};
    closeDocks();
    document.querySelectorAll('.sys-section').forEach(function (s) { s.style.display = 'none'; });
    var el = document.getElementById('sec-' + section);
    if (el) el.style.display = 'block';
    var active = null;
    nav.querySelectorAll('a[data-section]').forEach(function (a) {
      var on = a.dataset.section === section;
      a.classList.toggle('is-active', on);
      a.classList.toggle('active', on);
      if (on) active = a;
    });
    if (active) {
      if (bayKicker) bayKicker.textContent = active.dataset.kicker || '';
      if (bayTitle) bayTitle.textContent = active.dataset.title || '';
      if (bayLede) bayLede.textContent = active.dataset.lede || '';
    }
    if (opts.scroll && bay && window.matchMedia('(max-width: 768px)').matches) {
      bay.scrollIntoView({ block: 'start' });
    }
  }

  function fromHash(opts) {
    var h = (location.hash || '#general').replace('#', '');
    show(h in { general: 1, models: 1, memory: 1, tools: 1, mcp: 1, modules: 1, shared_channel: 1, users: 1 } ? h : 'general', opts);
  }

  nav.querySelectorAll('a[data-section]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      userPicked = true;
      location.hash = a.dataset.section;
    });
  });
  window.addEventListener('hashchange', function () {
    fromHash({ scroll: userPicked });
  });
  fromHash();

  var saveBtn = document.getElementById('saveGeneral');
  if (saveBtn) {
    var seg = document.getElementById('setApprovalsMode');
    if (seg) {
      seg.querySelectorAll('.seg-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
          seg.querySelectorAll('.seg-btn').forEach(function (b) {
            b.classList.remove('is-active');
            b.setAttribute('aria-checked', 'false');
          });
          btn.classList.add('is-active');
          btn.setAttribute('aria-checked', 'true');
        });
      });
    }
    saveBtn.addEventListener('click', async function () {
      try {
        var active = seg && seg.querySelector('.seg-btn.is-active');
        var mode = active ? active.getAttribute('data-mode') : 'smart';
        var data = await Tomo.api('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            max_tool_iterations: parseInt(document.getElementById('setMaxIter').value, 10),
            learning_enabled: document.getElementById('setLearning').checked,
            approvals_mode: mode || 'smart',
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
  var fReasoning = document.getElementById('profReasoningEfforts');
  var fEnabled = document.getElementById('profEnabled');
  var defaultId = '';

  function esc(s) { return Tomo.escapeHtml(s); }

  function parseReasoningEfforts(value) {
    return String(value || '').split(/\r?\n/).map(function (line) {
      return line.trim();
    }).filter(Boolean);
  }

  function rowHtml(p) {
    var b = '';
    if (p.id === defaultId) b += ' <span class="badge accent sm">default</span>';
    b += p.enabled ? (p.api_key_set ? ' <span class="badge ok sm">key set</span>' : ' <span class="badge muted">no key</span>') : ' <span class="badge muted">disabled</span>';
    var def = p.id === defaultId ? '' : ' <button class="btn ghost sm" type="button" data-act="default">Set default</button>';
    var efforts = Array.isArray(p.reasoning_efforts) ? p.reasoning_efforts.length : 0;
    var effortLabel = efforts ? (efforts + ' effort' + (efforts === 1 ? '' : 's')) : 'no custom effort';
    return '<div class="row" data-id="' + esc(p.id) + '"><div class="meta"><div class="title">' + esc(p.name) + ' <span class="faint mono">' + esc(p.id) + '</span></div><div class="desc">' + esc(p.model || '—') + ' · ' + esc(p.base_url || 'default host') + ' · ' + esc(effortLabel) + '</div></div><div class="machine-row-actions">' + b + def + ' <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function render(profiles, dId) {
    defaultId = dId || '';
    if (!listEl) return;
    if (!profiles.length) { listEl.innerHTML = '<div class="empty">No profiles yet — add one to enable chat.</div>'; }
    else { listEl.innerHTML = profiles.map(rowHtml).join(''); }
    var val = document.getElementById('map-models-val');
    var node = nav.querySelector('a[data-section="models"]');
    var def = profiles.filter(function (p) { return p.id === defaultId; })[0];
    if (val) {
      val.textContent = def ? (def.model || def.name || 'Default') : (profiles.length ? (profiles.length + ' profiles') : 'No default');
    }
    if (node) {
      var ready = def && def.enabled && def.api_key_set;
      node.setAttribute('data-state', ready ? 'ok' : (profiles.length ? 'warn' : 'off'));
    }
  }

  async function loadProfiles() {
    try {
      var d = await Tomo.api('/api/llm-profiles');
      if (d) render(d.profiles || [], d.default_id || '');
    } catch (e) { if (listEl) listEl.innerHTML = '<div class="empty">Could not load profiles.</div>'; }
  }

  function openForm(mode, p) {
    fMode.value = mode;
    document.getElementById('profileFormTitle').textContent = mode === 'add' ? 'New profile' : 'Inspect profile';
    if (mode === 'add') {
      fId.value = ''; fName.value = ''; fBase.value = ''; fKey.value = ''; fModel.value = ''; fReasoning.value = ''; fEnabled.checked = true;
      markSelected(listEl, '');
    } else {
      fId.value = p.id; fName.value = p.name || ''; fBase.value = p.base_url || ''; fKey.value = ''; fModel.value = p.model || ''; fReasoning.value = (p.reasoning_efforts || []).join('\n'); fEnabled.checked = !!p.enabled;
      markSelected(listEl, p.id);
    }
    formCard.classList.remove('hidden');
    if (fName) fName.focus();
  }

  var addBtn = document.getElementById('addProfileBtn');
  if (addBtn) addBtn.addEventListener('click', function () { openForm('add'); });
  var cancelProf = document.getElementById('profCancel');
  if (cancelProf) cancelProf.addEventListener('click', function () { formCard.classList.add('hidden'); });

  if (listEl) {
    listEl.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]');
      var row = e.target.closest('[data-id]');
      var pid = row ? row.dataset.id : '';
      if (btn) {
        var act = btn.dataset.act;
        if (act === 'default') {
          try { await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid) + '/default', { method: 'POST' }); Tomo.toast('Default set', 'ok'); loadProfiles(); } catch (er) { Tomo.toast((er && er.message) || 'Could not set default', 'err'); }
        } else if (act === 'delete') {
          if (!confirm('Delete profile "' + pid + '"?')) return;
          try { await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid), { method: 'DELETE' }); Tomo.toast('Profile deleted', 'ok'); closeDocks(); loadProfiles(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
        }
        return;
      }
      if (!pid) return;
      try { var p = await Tomo.api('/api/llm-profiles/' + encodeURIComponent(pid)); if (p) openForm('edit', p); } catch (er) { Tomo.toast('Could not load profile', 'err'); }
    });
  }

  var saveProf = document.getElementById('profSave');
  if (saveProf) {
    saveProf.addEventListener('click', async function () {
      var body = { name: fName.value.trim(), base_url: fBase.value.trim(), model: fModel.value.trim(), reasoning_efforts: parseReasoningEfforts(fReasoning.value), enabled: fEnabled.checked };
      var key = fKey.value;
      if (key && key.indexOf('•') === -1) body.api_key = key;
      try {
        if (fMode.value === 'add') {
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
  var kbSearch = document.getElementById('kbSearch');
  var kbSearchMeta = document.getElementById('kbSearchMeta');
  var kbSearchTimer = null;
  var kbSearchSeq = 0;

  function parseTags(s) {
    return String(s || '').split(',').map(function (t) { return t.trim(); }).filter(Boolean);
  }

  function kbRowHtml(e) {
    var tags = (e.tags || []).map(function (t) { return '<span class="badge muted sm">' + esc(t) + '</span>'; }).join(' ');
    var preview = (e.body || '').slice(0, 120);
    if ((e.body || '').length > 120) preview += '…';
    return '<div class="row" data-id="' + esc(e.id) + '"><div class="meta"><div class="title">' + esc(e.title) + ' <span class="faint mono">' + esc(e.id) + '</span></div><div class="desc">' + esc(preview) + (tags ? ' · ' + tags : '') + '</div></div><div class="machine-row-actions"><button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function renderKnowledge(entries, opts) {
    if (!kbList) return;
    opts = opts || {};
    var q = (opts.query || '').trim();
    if (kbSearchMeta) {
      if (q) {
        kbSearchMeta.hidden = false;
        kbSearchMeta.textContent = entries.length + ' hit' + (entries.length === 1 ? '' : 's');
      } else {
        kbSearchMeta.hidden = true;
        kbSearchMeta.textContent = '';
      }
    }
    if (!entries.length) {
      kbList.innerHTML = q
        ? '<div class="empty">No entries match “' + esc(q) + '”.</div>'
        : '<div class="empty">No knowledge entries yet.</div>';
    } else {
      kbList.innerHTML = entries.map(kbRowHtml).join('');
    }
    if (!q) {
      var val = document.getElementById('map-memory-val');
      var node = nav.querySelector('a[data-section="memory"]');
      if (val) val.textContent = entries.length ? (entries.length + ' entr' + (entries.length === 1 ? 'y' : 'ies')) : 'Empty';
      if (node) node.setAttribute('data-state', entries.length ? 'ok' : 'off');
    }
  }

  async function loadKnowledge(query) {
    var q = (query != null ? query : (kbSearch && kbSearch.value) || '').trim();
    var seq = ++kbSearchSeq;
    try {
      var path = q
        ? '/api/knowledge?q=' + encodeURIComponent(q) + '&limit=50'
        : '/api/knowledge';
      var d = await Tomo.api(path);
      if (seq !== kbSearchSeq) return;
      if (d) renderKnowledge(d.entries || [], { query: q });
    } catch (e) {
      if (seq !== kbSearchSeq) return;
      if (kbList) kbList.innerHTML = '<div class="empty">Could not load knowledge entries.</div>';
    }
  }

  if (kbSearch) {
    kbSearch.addEventListener('input', function () {
      clearTimeout(kbSearchTimer);
      kbSearchTimer = setTimeout(function () { loadKnowledge(kbSearch.value); }, 220);
    });
    kbSearch.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        kbSearch.value = '';
        loadKnowledge('');
      }
    });
  }

  function openKbForm(mode, e) {
    kbMode.value = mode;
    document.getElementById('knowledgeFormTitle').textContent = mode === 'add' ? 'New entry' : 'Inspect entry';
    if (mode === 'add') {
      kbId.value = ''; kbTitle.value = ''; kbBody.value = ''; kbTags.value = '';
      markSelected(kbList, '');
    } else {
      kbId.value = e.id; kbTitle.value = e.title || ''; kbBody.value = e.body || ''; kbTags.value = (e.tags || []).join(', ');
      markSelected(kbList, e.id);
    }
    kbFormCard.classList.remove('hidden');
    if (kbTitle) kbTitle.focus();
  }

  var addKb = document.getElementById('addKnowledgeBtn');
  if (addKb) addKb.addEventListener('click', function () { openKbForm('add'); });
  var cancelKb = document.getElementById('kbCancel');
  if (cancelKb) cancelKb.addEventListener('click', function () { kbFormCard.classList.add('hidden'); });

  var uploadKbBtn = document.getElementById('uploadKnowledgeBtn');
  var kbFileInput = document.getElementById('kbFileInput');
  var KB_MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
  var KB_ALLOWED_EXT = { pdf: 1, docx: 1, txt: 1, md: 1, markdown: 1 };
  if (uploadKbBtn && kbFileInput) {
    uploadKbBtn.addEventListener('click', function () { kbFileInput.click(); });
    kbFileInput.addEventListener('change', async function () {
      var f = kbFileInput.files && kbFileInput.files[0];
      kbFileInput.value = '';
      if (!f) return;
      var ext = (f.name.split('.').pop() || '').toLowerCase();
      if (!KB_ALLOWED_EXT[ext]) {
        Tomo.toast('Unsupported file type. Allowed: PDF, DOCX, TXT, MD', 'err');
        return;
      }
      if (f.size > KB_MAX_UPLOAD_BYTES) {
        Tomo.toast('File too large (max 20MB)', 'err');
        return;
      }
      if (f.size === 0) {
        Tomo.toast('File is empty', 'err');
        return;
      }
      var fd = new FormData();
      fd.append('file', f, f.name);
      uploadKbBtn.disabled = true;
      Tomo.toast('Parsing upload…', 'ok');
      try {
        var created = await Tomo.api('/api/knowledge/upload', { method: 'POST', body: fd });
        var msg = 'Uploaded "' + (created && created.title ? created.title : f.name) + '"';
        if (created && created.upload && created.upload.truncated) msg += ' (truncated)';
        Tomo.toast(msg, 'ok');
        var warns = created && created.upload && created.upload.warnings;
        if (warns && warns.length) {
          Tomo.toast(warns.slice(0, 2).join('; '), 'err');
        }
        loadKnowledge();
      } catch (err) {
        var detail = err && err.body && err.body.detail;
        Tomo.toast((typeof detail === 'string' ? detail : null) || (err && err.message) || 'Upload failed', 'err');
      } finally {
        uploadKbBtn.disabled = false;
      }
    });
  }

  if (kbList) {
    kbList.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]');
      var row = e.target.closest('[data-id]');
      var eid = row ? row.dataset.id : '';
      if (btn && btn.dataset.act === 'delete') {
        if (!confirm('Delete knowledge entry "' + eid + '"?')) return;
        try { await Tomo.api('/api/knowledge/' + encodeURIComponent(eid), { method: 'DELETE' }); Tomo.toast('Entry deleted', 'ok'); closeDocks(); loadKnowledge(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
        return;
      }
      if (btn || !eid) return;
      try { var ent = await Tomo.api('/api/knowledge/' + encodeURIComponent(eid)); if (ent) openKbForm('edit', ent); } catch (er) { Tomo.toast('Could not load entry', 'err'); }
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

  // ---- Login accounts (System → Accounts) ----
  var userList = document.getElementById('userList');
  var userFormCard = document.getElementById('userFormCard');
  var uMode = document.getElementById('userFormMode');
  var uId = document.getElementById('userId');
  var uUsername = document.getElementById('userUsername');
  var uDisplay = document.getElementById('userDisplayName');
  var uPass = document.getElementById('userPassword');
  var uEnabled = document.getElementById('userEnabled');
  var uEnabledRow = document.getElementById('userEnabledRow');

  function userRowHtml(u) {
    var badge = u.enabled ? '<span class="badge ok sm">enabled</span>' : '<span class="badge muted sm">disabled</span>';
    return '<div class="row" data-id="' + esc(u.id) + '"><div class="meta"><div class="title">' + esc(u.display_name || u.username) + ' <span class="faint mono">' + esc(u.username) + '</span></div><div class="desc mono" style="font-size:11px">' + esc(u.id) + '</div></div><div class="machine-row-actions">' + badge + ' <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function renderUsers(users) {
    if (!userList) return;
    if (!users.length) { userList.innerHTML = '<div class="empty">No accounts yet.</div>'; }
    else { userList.innerHTML = users.map(userRowHtml).join(''); }
    var val = document.getElementById('map-users-val');
    var node = nav.querySelector('a[data-section="users"]');
    var n = users.length;
    var on = users.filter(function (u) { return u.enabled; }).length;
    if (val) val.textContent = n ? (n + ' account' + (n === 1 ? '' : 's')) : 'None';
    if (node) node.setAttribute('data-state', on ? 'ok' : 'warn');
  }

  async function loadUsers() {
    try {
      var d = await Tomo.api('/api/users');
      if (d) {
        renderUsers(d.users || []);
        fillApiKeyUserSelect(d.users || []);
      }
    } catch (e) { if (userList) userList.innerHTML = '<div class="empty">Could not load accounts.</div>'; }
  }

  function openUserForm(mode, u) {
    uMode.value = mode;
    document.getElementById('userFormTitle').textContent = mode === 'add' ? 'New account' : 'Inspect account';
    if (mode === 'add') {
      uId.value = ''; uUsername.value = ''; uDisplay.value = ''; uPass.value = '';
      uUsername.disabled = false; uEnabled.checked = true;
      if (uEnabledRow) uEnabledRow.style.display = 'none';
      markSelected(userList, '');
    } else {
      uId.value = u.id; uUsername.value = u.username || ''; uDisplay.value = u.display_name || '';
      uPass.value = ''; uUsername.disabled = true; uEnabled.checked = !!u.enabled;
      if (uEnabledRow) uEnabledRow.style.display = '';
      markSelected(userList, u.id);
    }
    userFormCard.classList.remove('hidden');
    var focusEl = mode === 'add' ? uUsername : uDisplay;
    if (focusEl) focusEl.focus();
  }

  var addUser = document.getElementById('addUserBtn');
  if (addUser) addUser.addEventListener('click', function () { openUserForm('add'); });
  var cancelUser = document.getElementById('userCancel');
  if (cancelUser) cancelUser.addEventListener('click', function () { userFormCard.classList.add('hidden'); });

  if (userList) {
    userList.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]');
      var row = e.target.closest('[data-id]');
      var uid = row ? row.dataset.id : '';
      if (btn && btn.dataset.act === 'delete') {
        if (!confirm('Delete account "' + uid + '"?')) return;
        try { await Tomo.api('/api/users/' + encodeURIComponent(uid), { method: 'DELETE' }); Tomo.toast('Account deleted', 'ok'); closeDocks(); loadUsers(); loadApiKeys(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
        return;
      }
      if (btn || !uid) return;
      try { var u = await Tomo.api('/api/users/' + encodeURIComponent(uid)); if (u) openUserForm('edit', u); } catch (er) { Tomo.toast('Could not load account', 'err'); }
    });
  }

  var saveUser = document.getElementById('userSave');
  if (saveUser) {
    saveUser.addEventListener('click', async function () {
      try {
        if (uMode.value === 'add') {
          var username = uUsername.value.trim();
          var password = uPass.value;
          if (!username) { Tomo.toast('Username is required', 'err'); return; }
          if (!password || password.length < 4) { Tomo.toast('Password must be at least 4 characters', 'err'); return; }
          await Tomo.api('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              username: username,
              password: password,
              display_name: uDisplay.value.trim(),
            }),
          });
          Tomo.toast('Account created', 'ok');
        } else {
          var body = {
            display_name: uDisplay.value.trim(),
            enabled: !!uEnabled.checked,
          };
          if (uPass.value) body.password = uPass.value;
          await Tomo.api('/api/users/' + encodeURIComponent(uId.value), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          Tomo.toast('Account saved', 'ok');
        }
        userFormCard.classList.add('hidden');
        loadUsers();
      } catch (err) { Tomo.toast((err && err.message) || 'Could not save account', 'err'); }
    });
  }

  // ---- API keys ----
  var apiKeyList = document.getElementById('apiKeyList');
  var apiKeyFormCard = document.getElementById('apiKeyFormCard');
  var apiKeyRevealCard = document.getElementById('apiKeyRevealCard');
  var apiKeyUserSel = document.getElementById('apiKeyUserId');
  var apiKeyName = document.getElementById('apiKeyName');
  var apiKeyRevealToken = document.getElementById('apiKeyRevealToken');
  var cachedUsers = [];

  function fillApiKeyUserSelect(users) {
    cachedUsers = (users || []).filter(function (u) { return u.enabled; });
    if (!apiKeyUserSel) return;
    apiKeyUserSel.innerHTML = cachedUsers.map(function (u) {
      return '<option value="' + esc(u.id) + '">' + esc(u.username) + '</option>';
    }).join('');
  }

  function apiKeyRowHtml(k) {
    var meta = esc(k.name || 'API key') + ' · <span class="mono">' + esc(k.key_prefix) + '</span>';
    if (k.user_id) meta += ' · <span class="mono">' + esc(k.user_id) + '</span>';
    return '<div class="row" data-id="' + esc(k.id) + '"><div class="meta"><div class="title">' + meta + '</div></div><div><button class="btn ghost sm" type="button" data-act="revoke">Revoke</button></div></div>';
  }

  function renderApiKeys(keys) {
    if (!apiKeyList) return;
    if (!keys.length) { apiKeyList.innerHTML = '<div class="empty">No API keys yet.</div>'; return; }
    apiKeyList.innerHTML = keys.map(apiKeyRowHtml).join('');
  }

  async function loadApiKeys() {
    try {
      var d = await Tomo.api('/api/api-keys');
      if (d) renderApiKeys(d.keys || []);
    } catch (e) { if (apiKeyList) apiKeyList.innerHTML = '<div class="empty">Could not load API keys.</div>'; }
  }

  var addApiKey = document.getElementById('addApiKeyBtn');
  if (addApiKey) {
    addApiKey.addEventListener('click', function () {
      if (!cachedUsers.length) { Tomo.toast('Create an account first', 'err'); return; }
      if (apiKeyName) apiKeyName.value = '';
      if (apiKeyFormCard) apiKeyFormCard.classList.remove('hidden');
      if (apiKeyRevealCard) apiKeyRevealCard.classList.add('hidden');
    });
  }
  var apiKeyCancel = document.getElementById('apiKeyCancel');
  if (apiKeyCancel) apiKeyCancel.addEventListener('click', function () { if (apiKeyFormCard) apiKeyFormCard.classList.add('hidden'); });

  var apiKeySave = document.getElementById('apiKeySave');
  if (apiKeySave) {
    apiKeySave.addEventListener('click', async function () {
      var uid = apiKeyUserSel ? apiKeyUserSel.value : '';
      if (!uid) { Tomo.toast('Pick an account', 'err'); return; }
      try {
        var created = await Tomo.api('/api/api-keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: uid, name: (apiKeyName && apiKeyName.value.trim()) || '' }),
        });
        if (apiKeyFormCard) apiKeyFormCard.classList.add('hidden');
        if (created && created.token && apiKeyRevealToken && apiKeyRevealCard) {
          apiKeyRevealToken.textContent = created.token;
          apiKeyRevealCard.classList.remove('hidden');
        }
        Tomo.toast('API key created', 'ok');
        loadApiKeys();
      } catch (err) { Tomo.toast((err && err.message) || 'Could not create key', 'err'); }
    });
  }

  if (apiKeyList) {
    apiKeyList.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act="revoke"]'); if (!btn) return;
      var row = btn.closest('[data-id]'); var kid = row ? row.dataset.id : '';
      if (!kid || !confirm('Revoke this API key?')) return;
      try {
        await Tomo.api('/api/api-keys/' + encodeURIComponent(kid), { method: 'DELETE' });
        Tomo.toast('API key revoked', 'ok');
        loadApiKeys();
      } catch (er) { Tomo.toast((er && er.message) || 'Could not revoke', 'err'); }
    });
  }

  var copyBtn = document.getElementById('apiKeyCopyBtn');
  if (copyBtn) {
    copyBtn.addEventListener('click', async function () {
      var t = apiKeyRevealToken ? apiKeyRevealToken.textContent : '';
      if (!t) return;
      try {
        await navigator.clipboard.writeText(t);
        Tomo.toast('Copied', 'ok');
      } catch (e) { Tomo.toast('Could not copy', 'err'); }
    });
  }
  var revealDone = document.getElementById('apiKeyRevealDone');
  if (revealDone) {
    revealDone.addEventListener('click', function () {
      if (apiKeyRevealCard) apiKeyRevealCard.classList.add('hidden');
      if (apiKeyRevealToken) apiKeyRevealToken.textContent = '';
    });
  }

  loadUsers();
  loadApiKeys();
})();

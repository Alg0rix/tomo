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
    show(h in { general: 1, models: 1, memory: 1, tools: 1, modules: 1, shared_channel: 1, users: 1 } ? h : 'general');
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
      fId.value = ''; fName.value = ''; fBase.value = ''; fKey.value = ''; fModel.value = ''; fEnabled.checked = true;
    } else {
      fId.value = p.id; fName.value = p.name || ''; fBase.value = p.base_url || ''; fKey.value = ''; fModel.value = p.model || ''; fEnabled.checked = !!p.enabled;
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
      kbId.value = ''; kbTitle.value = ''; kbBody.value = ''; kbTags.value = '';
    } else {
      kbId.value = e.id; kbTitle.value = e.title || ''; kbBody.value = e.body || ''; kbTags.value = (e.tags || []).join(', ');
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
    return '<div class="row" data-id="' + esc(u.id) + '"><div class="meta"><div class="title">' + esc(u.display_name || u.username) + ' <span class="faint mono">' + esc(u.username) + '</span></div><div class="desc mono" style="font-size:11px">' + esc(u.id) + '</div></div><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' + badge + ' <button class="btn ghost sm" type="button" data-act="edit">Edit</button> <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function renderUsers(users) {
    if (!userList) return;
    if (!users.length) { userList.innerHTML = '<div class="empty">No accounts yet.</div>'; return; }
    userList.innerHTML = users.map(userRowHtml).join('');
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
    document.getElementById('userFormTitle').textContent = mode === 'add' ? 'Add account' : 'Edit account';
    if (mode === 'add') {
      uId.value = ''; uUsername.value = ''; uDisplay.value = ''; uPass.value = '';
      uUsername.disabled = false; uEnabled.checked = true;
      if (uEnabledRow) uEnabledRow.style.display = 'none';
    } else {
      uId.value = u.id; uUsername.value = u.username || ''; uDisplay.value = u.display_name || '';
      uPass.value = ''; uUsername.disabled = true; uEnabled.checked = !!u.enabled;
      if (uEnabledRow) uEnabledRow.style.display = '';
    }
    userFormCard.classList.remove('hidden');
  }

  var addUser = document.getElementById('addUserBtn');
  if (addUser) addUser.addEventListener('click', function () { openUserForm('add'); });
  var cancelUser = document.getElementById('userCancel');
  if (cancelUser) cancelUser.addEventListener('click', function () { userFormCard.classList.add('hidden'); });

  if (userList) {
    userList.addEventListener('click', async function (e) {
      var btn = e.target.closest('button[data-act]'); if (!btn) return;
      var row = btn.closest('[data-id]'); var uid = row ? row.dataset.id : ''; var act = btn.dataset.act;
      if (act === 'edit') {
        try { var u = await Tomo.api('/api/users/' + encodeURIComponent(uid)); if (u) openUserForm('edit', u); } catch (er) { Tomo.toast('Could not load account', 'err'); }
      } else if (act === 'delete') {
        if (!confirm('Delete account "' + uid + '"?')) return;
        try { await Tomo.api('/api/users/' + encodeURIComponent(uid), { method: 'DELETE' }); Tomo.toast('Account deleted', 'ok'); loadUsers(); loadApiKeys(); } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
      }
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

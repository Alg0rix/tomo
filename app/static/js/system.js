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
    show(h in { general: 1, models: 1, tools: 1, plugins: 1, hmads: 1, users: 1, shared_channel: 1, logs: 1 } ? h : 'general');
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
})();

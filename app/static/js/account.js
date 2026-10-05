/* Owned profile/keys/projects and explicit Admin assignments. */
(function () {
  'use strict';
  var root = document.getElementById('accountPage'); if (!root) return;
  var esc = Tomo.escapeHtml, uid = document.body.dataset.userId;
  function $(id) { return document.getElementById(id); }
  var resources = [], accounts = [], catalog = {};
  function api(path, method, data) { return Tomo.api(path, {method:method || 'GET', headers:{'Content-Type':'application/json'}, ...(data === undefined ? {} : {body:JSON.stringify(data)})}); }
  async function action(work) {
    $('accountStatus').textContent = '';
    try { await work(); } catch (e) { $('accountStatus').textContent = e.message || 'Operation unavailable'; }
  }
  function bindForm(id, work) { $(id).addEventListener('submit', function (e) { e.preventDefault(); var b = e.target.querySelector('[type="submit"]'); b.disabled = true; action(function () { return work(e.target); }).finally(function () { b.disabled = false; }); }); }
  async function keys() {
    var data = await Tomo.api('/api/api-keys?user_id=' + encodeURIComponent(uid));
    $('personalKeys').innerHTML = (data.keys || []).map(function (k) { return '<div class="access-row"><span>' + esc(k.name || 'Personal key') + ' · ' + esc(k.key_prefix || k.id) + '</span><button class="btn ghost sm" data-key="' + esc(k.id) + '">Revoke</button></div>'; }).join('');
  }
  async function telegram() {
    var data = await Tomo.api('/api/users/' + encodeURIComponent(uid) + '/telegram');
    $('telegramLinks').innerHTML = (data.links || []).map(function (l) { return '<div class="access-row"><span>Chat ' + esc(l.chat_id) + ' · linked ' + esc(new Date(l.created_at * 1000).toLocaleString()) + '</span><button class="btn ghost sm" data-tg-unlink="' + esc(l.chat_id) + '">Unlink</button></div>'; }).join('') || '<p class="access-note">No linked Telegram chats.</p>';
  }
  bindForm('telegramLink', async function () {
    var data = await api('/api/users/' + encodeURIComponent(uid) + '/telegram/link-code', 'POST');
    $('newTelegramCode').textContent = 'Send this in a private DM to the bot (once, within 10 minutes):\n' + data.command;
    $('newTelegramCode').hidden = false;
  });
  async function projects() {
    resources = (await Tomo.api('/api/access/resources')).workplaces;
    $('personalProjects').innerHTML = resources.map(function (w) {
      var owned = w.storage_kind === 'project' && (w.owner_user_id === uid || document.body.dataset.role === 'admin');
      return '<div class="access-project"><strong>' + esc(w.name) + '</strong><p class="access-note">' + esc(Tomo.access.metadata(w)) + '</p>' +
        (owned ? '<details data-project="' + esc(w.id) + '"><summary>Manage sharing</summary><p class="access-note">Recipients cannot reshare. Revocation stops affected managed work; already copied data is not recalled.</p><form class="access-row" data-share="' + esc(w.id) + '"><input class="input" name="username" aria-label="Recipient username" placeholder="Recipient username" required><select class="input" name="permission" aria-label="Share permission"><option value="read">Read-only</option><option value="read_write">Read-write</option></select><button class="btn ghost" type="submit">Share</button></form><div data-shares></div></details>' : '') + '</div>';
    }).join('');
    root.querySelectorAll('[data-project]').forEach(function (details) { details.addEventListener('toggle', function () { if (details.open) action(function () { return shares(details); }); }); });
  }
  async function shares(details) {
    var data = await Tomo.api('/api/projects/' + encodeURIComponent(details.dataset.project) + '/shares');
    details.querySelector('[data-shares]').innerHTML = data.shares.map(function (g) { return '<div class="access-row"><span>' + esc(g.username) + ' · ' + esc((g.permission === 'read' ? 'Read-only' : 'Read-write') + (g.state === 'pending' ? ' · Teardown pending; access unavailable' : '')) + '</span><button class="btn ghost sm" data-unshare="' + esc(g.user_id) + '">' + (g.state === 'pending' ? 'Retry revocation' : 'Revoke') + '</button></div>'; }).join('') || '<p class="access-note">Not shared.</p>';
  }
  bindForm('personalProfile', async function (form) {
    var data = {display_name:form.elements.display_name.value}; if (form.elements.password.value) data.password = form.elements.password.value;
    await api('/api/me', 'PUT', data); form.elements.password.value = ''; Tomo.toast('Profile saved', 'ok');
  });
  bindForm('personalKey', async function (form) {
    var k = await api('/api/api-keys', 'POST', {user_id:uid, name:form.elements.name.value});
    $('newKeyToken').textContent = 'Copy this token now; it will not be shown again.\n' + k.token; $('newKeyToken').hidden = false; await keys();
  });
  $('createProject').onclick = function () { action(async function () { if (await Tomo.access.createProject()) await projects(); }); };
  root.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-share]'); if (!form) return; event.preventDefault();
    action(async function () { await api('/api/projects/' + encodeURIComponent(form.dataset.share) + '/shares', 'POST', {username:form.elements.username.value, permission:form.elements.permission.value}); await shares(form.closest('details')); });
  });
  root.addEventListener('click', function (event) {
    var button = event.target.closest('[data-key],[data-unshare],[data-revoke-grant],[data-tg-unlink]'); if (!button) return;
    if (!window.confirm('Revoke this access? Affected managed work will stop.')) return;
    action(async function () {
      if (button.dataset.key) { await api('/api/api-keys/' + encodeURIComponent(button.dataset.key), 'DELETE'); $('newKeyToken').hidden = true; await keys(); }
      else if (button.dataset.tgUnlink) { await api('/api/users/' + encodeURIComponent(uid) + '/telegram/' + encodeURIComponent(button.dataset.tgUnlink), 'DELETE'); await telegram(); Tomo.toast('Telegram unlinked', 'ok'); }
      else if (button.dataset.unshare) { var d = button.closest('details'); await api('/api/projects/' + encodeURIComponent(d.dataset.project) + '/shares/' + encodeURIComponent(button.dataset.unshare), 'DELETE'); await shares(d); }
      else { await api(grantPath(button.dataset.type, button.dataset.resource), 'DELETE'); await grants(); }
    });
  });
  function grantPath(type, resource) { return '/api/users/' + encodeURIComponent($('managedAccount').value) + '/grants/' + encodeURIComponent(type) + '/' + encodeURIComponent(resource); }
  async function grants() {
    var id = $('managedAccount').value, user = accounts.find(function (u) { return u.id === id; }); if (!user) return;
    $('managedRole').elements.role.value = user.role;
    $('accountGrants').textContent = 'Loading assignments…';
    ['managedRole', 'resourceAssignment'].forEach(function (f) { Array.from($(f).elements).forEach(function (e) { e.disabled = true; }); });
    var results = await Promise.all([
      Tomo.api('/api/users/' + encodeURIComponent(id) + '/grants'),
      Tomo.api('/api/users/' + encodeURIComponent(id) + '/unrestricted-destinations').catch(function () { return {workplaces:[]}; })
    ]);
    if (id !== $('managedAccount').value) return;
    var data = results[0]; catalog.unrestricted = results[1].workplaces;
    ['managedRole', 'resourceAssignment'].forEach(function (f) { Array.from($(f).elements).forEach(function (e) { e.disabled = false; }); });
    // Restore the permission-type restrictions after enabling form controls.
    assignmentOptions();
    $('accountGrants').innerHTML = (data.grants || []).filter(function (g) { return g.state !== 'revoked'; }).map(function (g) {
      var item = (catalog[g.resource_type] || []).find(function (r) { return r.id === g.resource_id; });
      return '<div class="access-row"><span>' + esc(g.resource_type + ' · ' + (item ? item.name : 'Unavailable resource') + ' · ' + g.permission + ' · ' + g.state) + '</span><button class="btn ghost sm" data-revoke-grant="1" data-type="' + esc(g.resource_type) + '" data-resource="' + esc(g.resource_id) + '">Revoke</button></div>';
    }).join('') || '<p class="access-note">No explicit assignments.</p>';
  }
  function assignmentOptions() {
    var form = $('resourceAssignment'), type = form.elements.type.value;
    form.elements.resource.innerHTML = (catalog[type] || []).map(function (r) { return '<option value="' + esc(r.id) + '">' + esc(r.name) + '</option>'; }).join('');
    Array.from(form.elements.permission.options).forEach(function (o) { o.disabled = type === 'workplace' ? o.value === 'use' : o.value !== 'use'; });
    form.elements.permission.value = type === 'workplace' ? 'read' : 'use';
    root.querySelector('[data-grant-warning]').hidden = type !== 'unrestricted';
  }
  async function administration() {
    if (!$('accountAdministration')) return;
    accounts = (await Tomo.api('/api/users')).users;
    catalog.agent = (await Tomo.api('/api/agents')).agents;
    catalog.model = (await Tomo.api('/api/llm-profiles')).profiles;
    // Folder grants never include another account's personal space. The
    // destination catalog separately offers a generic owned-personal ID for
    // issuing an explicit unrestricted grant, not permission to browse it.
    catalog.workplace = resources.filter(function (w) { return w.storage_kind !== 'personal'; });
    catalog.unrestricted = [];
    $('managedAccount').innerHTML = accounts.filter(function (u) { return u.enabled; }).map(function (u) { return '<option value="' + esc(u.id) + '">' + esc(u.username) + ' · ' + esc(u.role) + '</option>'; }).join('');
    $('managedAccount').onchange = function () { action(grants); };
    $('resourceAssignment').elements.type.onchange = assignmentOptions;
    assignmentOptions(); await grants();
    bindForm('managedRole', async function (form) {
      await api('/api/users/' + encodeURIComponent($('managedAccount').value), 'PUT', {role:form.elements.role.value});
      accounts = (await Tomo.api('/api/users')).users;
      Array.from($('managedAccount').options).forEach(function (o) {
        var account = accounts.find(function (u) { return u.id === o.value; });
        if (account) o.textContent = account.username + ' · ' + account.role;
      });
      await grants(); Tomo.toast('Role saved', 'ok');
      if ($('managedAccount').value === uid) window.location.reload();
    });
    bindForm('resourceAssignment', async function (form) {
      var type = form.elements.type.value;
      if (type === 'unrestricted' && !window.confirm('Grant OS-account access at this destination? Cross-user confidentiality is not guaranteed.')) return;
      await api(grantPath(type, form.elements.resource.value), 'PUT', {permission:form.elements.permission.value}); await grants();
    });
  }
  action(async function () {
    var me = await Tomo.api('/api/me'); $('personalProfile').elements.display_name.value = me.display_name || me.username;
    await keys(); await telegram(); await projects(); await administration();
  });
})();

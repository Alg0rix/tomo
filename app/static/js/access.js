/* Shared safe resource picker and owned-chat activation. No host paths. */
(function () {
  'use strict';
  var esc = Tomo.escapeHtml;
  var uid = document.body.dataset.userId;
  function group(w) {
    if (w.kind !== 'local') return 'Connected machines / remote workplaces';
    if (w.owner_user_id !== uid && w.owner_user_id) return 'Shared with you';
    if (w.storage_kind === 'personal') return 'Personal space';
    if (w.storage_kind === 'project') return 'Personal projects';
    return 'Registered folders';
  }
  function metadata(w) {
    if (w.kind !== 'local') {
      var exec = !w.online ? 'offline · execution refused' :
        !w.remote_exec_ok ? 'update connector (needs exec-context-v1 ≥ 0.4.0)' :
        (w.remote_sandbox_ok ? 'restricted-ready' : 'unrestricted-only (grant + acknowledgement required)');
      return (w.permission === 'read_write' ? 'Read-write' : 'Read-only') + ' · ' +
        (w.destination_id || w.id) + ' · ' + exec;
    }
    return (w.permission === 'read_write' ? 'Read-write' : 'Read-only') + ' · ' +
      (w.destination_id || 'local') + ' · ' +
      (w.storage_kind === 'external' ? 'restricted execution unavailable; select Personal space for restricted execution, or use authorized unrestricted execution' : (w.status || 'ready'));
  }
  function label(w) { return (w.name || 'Working location') + ' · ' + metadata(w); }
  function fill(select, resources, selected) {
    var groups = {};
    resources.forEach(function (w) { (groups[group(w)] || (groups[group(w)] = [])).push(w); });
    var order = ['Personal space', 'Personal projects', 'Shared with you', 'Registered folders', 'Connected machines / remote workplaces'];
    select.innerHTML = '<option value="">Personal space (default)</option>' + order.filter(function (name) { return groups[name]; }).map(function (name) {
      return '<optgroup label="' + esc(name) + '">' + groups[name].map(function (w) {
        return '<option value="' + esc(w.id) + '">' + esc(label(w)) + '</option>';
      }).join('') + '</optgroup>';
    }).join('');
    select.value = resources.some(function (w) { return w.id === selected; }) ? selected : '';
  }
  async function createProject() {
    var name = window.prompt('Project name');
    if (!name || !name.trim()) return null;
    return (await Tomo.api('/api/projects', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name.trim()})})).workplace;
  }
  function updateBar(session, resources) {
    var button = document.getElementById('chatAccessButton'), mode = document.getElementById('chatExecutionMode');
    if (!button) return;
    var resource = resources.find(function (w) { return w.id === session.workplace_id; });
    var legacy = document.body.dataset.role === 'admin' && !session.workplace_id && session.access_generation === 0;
    var unrestricted = legacy || session.execution_mode === 'unrestricted';
    button.textContent = 'Working location: ' + (resource ? resource.name : session.workplace_id ? 'Unavailable resource' : legacy ? 'Legacy host' : 'Personal space');
    button.title = resource ? metadata(resource) : legacy ? 'Legacy OS-account access; cross-user confidentiality is not guaranteed' : 'Persistent private files';
    button.disabled = !!session.access_pending;
    mode.textContent = (session.access_pending ? 'Teardown pending' :
      unrestricted ? 'Unrestricted · OS account access' : 'Restricted') +
      ' · ' + (resource ? (resource.destination_id || 'local') + ' / ' + resource.name : legacy ? 'local / legacy host' : 'local');
    mode.className = 'badge sm ' + (unrestricted || session.access_pending ? 'danger' : 'ok');
  }
  async function editChat(wrap) {
    var sid = wrap.dataset.sessionId;
    if (!sid || sid.indexOf('draft') === 0) { Tomo.toast('Send a message to create the chat first. Choose its starting folder in New chat.', 'err'); return; }
    var state = await Tomo.api('/api/sessions/' + encodeURIComponent(sid) + '/access');
    var admin = document.body.dataset.role === 'admin';
    var dialog = document.createElement('dialog'); dialog.className = 'access-dialog';
    dialog.setAttribute('aria-labelledby', 'accessDialogTitle');
    dialog.innerHTML = '<h2 id="accessDialogTitle">Working location</h2><p class="access-note">The active folder is the default destination. Changing access stops managed work; it does not move files. Shared files may be edited concurrently; edits are not automatically merged.</p>' +
      '<form><label class="field">Active folder<select class="input" name="active" required></select></label>' +
      '<button type="button" class="btn ghost sm" data-create>+ Create project</button><h3>Additional access</h3><div data-resources></div>' +
      '<label class="field">Execution mode<select class="input" name="mode"><option value="restricted">Restricted container</option><option value="unrestricted">Unrestricted at this destination</option></select></label>' +
      '<div class="access-warning" data-warning hidden><strong>OS-account confidentiality warning</strong><p>Unrestricted execution can access everything the server OS account can reach, including other users’ data and credentials. Folder grants are not a filesystem boundary in this mode. Tomo cannot guarantee cross-user confidentiality, rollback, or termination of escaped processes.</p><label><input type="checkbox" name="ack"> I understand and explicitly activate unrestricted execution at this destination.</label></div>' +
      '<p class="access-note">Admin chats use host execution by default. Restricted execution requires a non-root sandbox broker and bounded storage; it never falls back to the host.</p>' +
      '<p class="access-status" role="alert"></p><div class="access-actions"><button type="button" class="btn ghost" data-cancel>Cancel</button><button type="submit" class="btn primary">Save access</button></div></form>';
    document.body.appendChild(dialog);
    var form = dialog.querySelector('form'), active = form.elements.active, mode = form.elements.mode;
    var status = dialog.querySelector('.access-status');
    dialog.querySelector('[data-warning] label').hidden = admin;
    function render() {
      fill(active, state.workplaces, state.active_workplace_id);
      // The default is an actual resource in a persisted chat, never empty.
      active.querySelector('option[value=""]').remove();
      dialog.querySelector('[data-resources]').innerHTML = state.workplaces.map(function (w) {
        return '<label class="access-resource"><input type="checkbox" name="additional" value="' + esc(w.id) + '"' +
          ((state.additional_workplace_ids || []).indexOf(w.id) >= 0 ? ' checked' : '') + '><span>' + esc(w.name) + '<small>' + esc(metadata(w)) + '</small></span></label>';
      }).join('');
      sync();
    }
    function sync() {
      dialog.querySelectorAll('input[name="additional"]').forEach(function (cb) {
        cb.disabled = cb.value === active.value;
        if (cb.disabled) cb.checked = false;
      });
      var allowed = (state.unrestricted_workplace_ids || []).indexOf(active.value) >= 0;
      mode.querySelector('option[value="unrestricted"]').disabled = !allowed;
      if (!allowed) mode.value = 'restricted';
      dialog.querySelector('[data-warning]').hidden = mode.value !== 'unrestricted';
      form.elements.ack.required = mode.value === 'unrestricted' && !admin;
    }
    mode.value = state.execution_mode;
    render();
    active.addEventListener('change', function () { form.elements.ack.checked = false; sync(); });
    mode.addEventListener('change', sync);
    dialog.querySelector('[data-create]').addEventListener('click', async function () {
      try { var w = await createProject(); if (w) { state.workplaces.push(w); state.active_workplace_id = w.id; render(); } }
      catch (e) { status.textContent = e.message; }
    });
    dialog.querySelector('[data-cancel]').onclick = function () { dialog.close(); };
    dialog.addEventListener('close', function () { dialog.remove(); });
    form.addEventListener('submit', async function (event) {
      event.preventDefault(); status.textContent = ''; var save = form.querySelector('[type="submit"]'); save.disabled = true;
      try {
        var result = await Tomo.api('/api/sessions/' + encodeURIComponent(sid) + '/access', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          active_workplace_id:active.value, additional_workplace_ids:Array.from(form.querySelectorAll('[name="additional"]:checked:not(:disabled)')).map(function (cb) { return cb.value; }),
          execution_mode:mode.value, unrestricted_acknowledged:form.elements.ack.checked
        })});
        wrap.dataset.workplaceId = result.workplace_id;
        updateBar(result, state.workplaces); dialog.close();
        wrap.dispatchEvent(new CustomEvent('tomo:access-changed'));
      } catch (e) { status.textContent = e.message || 'Access change failed; affected execution may remain unavailable until teardown is confirmed.'; }
      finally { save.disabled = false; }
    });
    dialog.showModal();
  }
  Tomo.access = {fill:fill, label:label, metadata:metadata, createProject:createProject, updateBar:updateBar};
  var button = document.getElementById('chatAccessButton');
  if (button) button.addEventListener('click', function () {
    editChat(document.getElementById('sessionChat')).catch(function (e) { Tomo.toast(e.message || 'Access unavailable', 'err'); });
  });
})();

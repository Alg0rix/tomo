/* mcp.js — System → MCP: server CRUD, capability toggles, resource/prompt actions */
(function () {
  "use strict";

  var listEl = document.getElementById('mcpServerList');
  if (!listEl) return;

  var formCard = document.getElementById('mcpFormCard');
  var fMode = document.getElementById('mcpFormMode');
  var fId = document.getElementById('mcpServerId');
  var fName = document.getElementById('mcpName');
  var fTransport = document.getElementById('mcpTransport');
  var fCommand = document.getElementById('mcpCommand');
  var fArgs = document.getElementById('mcpArgs');
  var fUrl = document.getElementById('mcpUrl');
  var fEnabled = document.getElementById('mcpEnabled');
  var stdioFields = document.getElementById('mcpStdioFields');
  var httpFields = document.getElementById('mcpHttpFields');
  var envRows = document.getElementById('mcpEnvRows');
  var headerRows = document.getElementById('mcpHeaderRows');
  var refreshBtn = document.getElementById('mcpRefreshBtn');
  var statusLine = document.getElementById('mcpStatusLine');
  var capWrap = document.getElementById('mcpCapabilities');
  var toolsList = document.getElementById('mcpToolsList');
  var resourcesList = document.getElementById('mcpResourcesList');
  var promptsList = document.getElementById('mcpPromptsList');
  var capResult = document.getElementById('mcpCapResult');

  function esc(s) { return Tomo.escapeHtml(s); }

  function addKvRow(container, key, value) {
    var row = document.createElement('div');
    row.className = 'mcp-kv-row';
    row.innerHTML =
      '<input class="input" placeholder="KEY" value="' + esc(key || '') + '" data-kv="key">' +
      '<input class="input" type="password" placeholder="value" value="' + esc(value || '') + '" data-kv="value">' +
      '<button class="btn ghost sm" type="button" data-kv="remove">✕</button>';
    row.querySelector('[data-kv="remove"]').addEventListener('click', function () { row.remove(); });
    container.appendChild(row);
  }

  function readKvRows(container) {
    var out = {};
    container.querySelectorAll('.mcp-kv-row').forEach(function (row) {
      var k = row.querySelector('[data-kv="key"]').value.trim();
      var v = row.querySelector('[data-kv="value"]').value;
      if (k) out[k] = v;
    });
    return out;
  }

  var addEnvBtn = document.getElementById('mcpAddEnvRow');
  var addHeaderBtn = document.getElementById('mcpAddHeaderRow');
  if (addEnvBtn) addEnvBtn.addEventListener('click', function () { addKvRow(envRows, '', ''); });
  if (addHeaderBtn) addHeaderBtn.addEventListener('click', function () { addKvRow(headerRows, '', ''); });

  function syncTransportFields() {
    var isStdio = fTransport.value === 'stdio';
    stdioFields.classList.toggle('hidden', !isStdio);
    httpFields.classList.toggle('hidden', isStdio);
  }
  fTransport.addEventListener('change', syncTransportFields);

  function statusBadgeCls(s) {
    if (s === 'connected') return 'ok';
    if (s === 'error') return 'danger';
    return 'muted';
  }

  function rowHtml(s) {
    var badges = (s.enabled ? '' : ' <span class="badge muted sm">disabled</span>') +
      ' <span class="badge ' + statusBadgeCls(s.status) + ' sm">' + esc(s.status || 'unknown') + '</span>';
    var target = s.transport === 'stdio' ? (s.command || '') : (s.url || '');
    return '<div class="row" data-id="' + esc(s.id) + '"><div class="meta"><div class="title">' + esc(s.name) +
      ' <span class="faint mono">' + esc(s.id) + '</span></div><div class="desc">' + esc(s.transport) + ' · ' +
      esc(target) + (s.status_message ? (' · ' + esc(s.status_message)) : '') + '</div></div>' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' + badges +
      ' <button class="btn ghost sm" type="button" data-act="edit">Edit</button>' +
      ' <button class="btn ghost sm" type="button" data-act="delete">Delete</button></div></div>';
  }

  function render(servers) {
    if (!servers.length) {
      listEl.innerHTML = '<div class="empty">No MCP servers yet — add one to give agents new tools.</div>';
      return;
    }
    listEl.innerHTML = servers.map(rowHtml).join('');
  }

  async function loadServers() {
    try {
      var d = await Tomo.api('/api/mcp-servers');
      if (d) render(d.servers || []);
    } catch (e) {
      listEl.innerHTML = '<div class="empty">Could not load MCP servers.</div>';
    }
  }

  function capItemRow(item, extraAction) {
    var toggle = '<label class="toggle"><input type="checkbox" data-item-id="' + esc(item.id) + '" ' +
      (item.enabled ? 'checked' : '') + '><span class="track"></span></label>';
    return '<div class="row mcp-cap-row"><div class="meta"><div class="title">' + esc(item.title || item.name) +
      '</div><div class="desc">' + esc(item.description || item.uri || '') + '</div></div>' +
      '<div style="display:flex;align-items:center;gap:8px">' + (extraAction || '') + toggle + '</div></div>';
  }

  function showCapResult(d) {
    var text;
    if (d && d.contents) {
      text = d.contents.map(function (c) {
        return c.kind === 'text' ? c.text : ('[' + c.mime_type + ', ' + (c.size_base64_chars || 0) + ' base64 chars]');
      }).join('\n\n');
    } else if (d && d.messages) {
      text = d.messages.map(function (m) { return m.role + ': ' + m.text; }).join('\n\n');
    } else {
      text = JSON.stringify(d, null, 2);
    }
    capResult.textContent = text;
    capResult.classList.remove('hidden');
  }

  function wireCapabilityActions(serverId) {
    capWrap.querySelectorAll('input[data-item-id]').forEach(function (cb) {
      cb.addEventListener('change', async function () {
        try {
          await Tomo.api(
            '/api/mcp-servers/' + encodeURIComponent(serverId) + '/items/' + encodeURIComponent(cb.dataset.itemId),
            { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: cb.checked }) }
          );
        } catch (e) {
          Tomo.toast((e && e.message) || 'Could not update tool', 'err');
          cb.checked = !cb.checked;
        }
      });
    });
    capWrap.querySelectorAll('[data-cap-act="read"]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        try {
          var d = await Tomo.api(
            '/api/mcp-servers/' + encodeURIComponent(serverId) + '/resources/read',
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uri: btn.dataset.uri }) }
          );
          showCapResult(d);
        } catch (e) { Tomo.toast((e && e.message) || 'Could not read resource', 'err'); }
      });
    });
    capWrap.querySelectorAll('[data-cap-act="use"]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        try {
          var d = await Tomo.api(
            '/api/mcp-servers/' + encodeURIComponent(serverId) + '/prompts/get',
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: btn.dataset.name, arguments: {} }) }
          );
          showCapResult(d);
        } catch (e) { Tomo.toast((e && e.message) || 'Could not load prompt', 'err'); }
      });
    });
  }

  function renderCapabilities(items) {
    var tools = items.filter(function (i) { return i.kind === 'tool'; });
    var resources = items.filter(function (i) { return i.kind === 'resource' || i.kind === 'resource_template'; });
    var prompts = items.filter(function (i) { return i.kind === 'prompt'; });
    toolsList.innerHTML = tools.length
      ? tools.map(function (i) { return capItemRow(i); }).join('')
      : '<div class="empty">No tools discovered.</div>';
    resourcesList.innerHTML = resources.length
      ? resources.map(function (i) {
        var action = i.kind === 'resource'
          ? '<button class="btn ghost sm" type="button" data-cap-act="read" data-uri="' + esc(i.uri) + '">Read</button>'
          : '';
        return capItemRow(i, action);
      }).join('')
      : '<div class="empty">No resources discovered.</div>';
    promptsList.innerHTML = prompts.length
      ? prompts.map(function (i) {
        var action = '<button class="btn ghost sm" type="button" data-cap-act="use" data-name="' + esc(i.name) + '">Use</button>';
        return capItemRow(i, action);
      }).join('')
      : '<div class="empty">No prompts discovered.</div>';
    capWrap.classList.remove('hidden');
  }

  async function openForm(mode, s) {
    fMode.value = mode;
    document.getElementById('mcpFormTitle').textContent = mode === 'add' ? 'Add MCP server' : 'Edit MCP server';
    envRows.innerHTML = '';
    headerRows.innerHTML = '';
    capWrap.classList.add('hidden');
    capResult.classList.add('hidden');
    refreshBtn.classList.toggle('hidden', mode === 'add');
    statusLine.textContent = '';

    if (mode === 'add') {
      fId.value = ''; fName.value = ''; fTransport.value = 'stdio';
      fCommand.value = ''; fArgs.value = ''; fUrl.value = ''; fEnabled.checked = true;
      syncTransportFields();
      formCard.classList.remove('hidden');
      return;
    }

    fId.value = s.id;
    fName.value = s.name || '';
    fTransport.value = s.transport || 'stdio';
    fCommand.value = s.command || '';
    fArgs.value = (s.args || []).join(', ');
    fUrl.value = s.url || '';
    fEnabled.checked = !!s.enabled;
    syncTransportFields();
    (s.env_keys || []).forEach(function (k) { addKvRow(envRows, k, '••••'); });
    (s.headers_keys || []).forEach(function (k) { addKvRow(headerRows, k, '••••'); });
    statusLine.textContent = (s.status || 'unknown') + (s.status_message ? (' — ' + s.status_message) : '');
    formCard.classList.remove('hidden');

    try {
      var full = await Tomo.api('/api/mcp-servers/' + encodeURIComponent(s.id));
      if (full) {
        renderCapabilities(full.items || []);
        wireCapabilityActions(s.id);
      }
    } catch (e) {
      // Discovery/connection may be down — form still shows saved config.
    }
  }

  var addBtn = document.getElementById('addMcpServerBtn');
  if (addBtn) addBtn.addEventListener('click', function () { openForm('add'); });
  var cancelBtn = document.getElementById('mcpCancel');
  if (cancelBtn) cancelBtn.addEventListener('click', function () { formCard.classList.add('hidden'); });

  listEl.addEventListener('click', async function (e) {
    var btn = e.target.closest('button[data-act]');
    if (!btn) return;
    var row = btn.closest('[data-id]');
    var sid = row ? row.dataset.id : '';
    var act = btn.dataset.act;
    if (act === 'edit') {
      try {
        var s = await Tomo.api('/api/mcp-servers/' + encodeURIComponent(sid));
        if (s) openForm('edit', s);
      } catch (er) { Tomo.toast('Could not load server', 'err'); }
    } else if (act === 'delete') {
      if (!confirm('Delete MCP server "' + sid + '"? This removes its saved tools too.')) return;
      try {
        await Tomo.api('/api/mcp-servers/' + encodeURIComponent(sid), { method: 'DELETE' });
        Tomo.toast('Server deleted', 'ok');
        formCard.classList.add('hidden');
        loadServers();
      } catch (er) { Tomo.toast((er && er.message) || 'Could not delete', 'err'); }
    }
  });

  function formBody() {
    var body = { name: fName.value.trim(), transport: fTransport.value, enabled: fEnabled.checked };
    if (fTransport.value === 'stdio') {
      body.command = fCommand.value.trim();
      body.args = fArgs.value.split(',').map(function (a) { return a.trim(); }).filter(Boolean);
      body.env = readKvRows(envRows);
    } else {
      body.url = fUrl.value.trim();
      body.headers = readKvRows(headerRows);
    }
    return body;
  }

  var saveBtn = document.getElementById('mcpSave');
  if (saveBtn) {
    saveBtn.addEventListener('click', async function () {
      var body = formBody();
      if (!body.name) { Tomo.toast('Name is required', 'err'); return; }
      try {
        var saved;
        if (fMode.value === 'add') {
          saved = await Tomo.api('/api/mcp-servers', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
          Tomo.toast('MCP server created', 'ok');
        } else {
          saved = await Tomo.api('/api/mcp-servers/' + encodeURIComponent(fId.value), {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
          Tomo.toast('MCP server saved', 'ok');
        }
        loadServers();
        // Stay open on the saved row — shows fresh status/capabilities without
        // discarding config even if the connection attempt itself failed.
        if (saved) openForm('edit', saved);
      } catch (e) { Tomo.toast((e && e.message) || 'Could not save MCP server', 'err'); }
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async function () {
      if (!fId.value) return;
      try {
        var s = await Tomo.api('/api/mcp-servers/' + encodeURIComponent(fId.value) + '/refresh', { method: 'POST' });
        Tomo.toast('Refreshed', 'ok');
        loadServers();
        if (s) openForm('edit', s);
      } catch (e) { Tomo.toast((e && e.message) || 'Could not refresh', 'err'); }
    });
  }

  loadServers();
})();

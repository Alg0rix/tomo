/* agent_detail.js — studio tab switching + config/tools save */
(function () {
  "use strict";
  document.querySelectorAll('.agent-studio-tabs .pill-tab[data-panel]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var panel = tab.dataset.panel;
      document.querySelectorAll('.pill-tab[data-panel]').forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      document.querySelectorAll('.agent-studio-panel').forEach(function (p) { p.classList.add('hidden'); });
      var el = document.getElementById('panel-' + panel);
      if (el) el.classList.remove('hidden');
      if (panel === 'chat') {
        var wrap = document.querySelector('.chat-wrap');
        if (wrap && window.TomoChat) TomoChat.init(wrap);
      }
      if (panel === 'artifacts') loadArtifacts(1);
    });
  });

  var artifactsPage = 1;
  function artifactsSessionId() {
    var panel = document.getElementById('panel-artifacts');
    var fromPanel = panel ? panel.dataset.sessionId : '';
    var wrap = document.querySelector('.chat-wrap');
    var fromChat = wrap ? wrap.dataset.sessionId : '';
    return fromChat || fromPanel || '';
  }
  function formatBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }
  async function loadArtifacts(page) {
    var sessionId = artifactsSessionId();
    var grid = document.getElementById('artifactsGrid');
    var empty = document.getElementById('artifactsEmpty');
    var pager = document.getElementById('artifactsPager');
    if (!grid) return;
    if (!sessionId) {
      grid.innerHTML = '';
      if (empty) {
        empty.classList.remove('hidden');
        empty.textContent = 'No active session yet — start a chat first.';
      }
      if (pager) pager.textContent = '';
      return;
    }
    var panel = document.getElementById('panel-artifacts');
    if (panel) panel.dataset.sessionId = sessionId;
    artifactsPage = page || 1;
    var q = (document.getElementById('artifactsSearch') || {}).value || '';
    var sort = (document.getElementById('artifactsSort') || {}).value || 'newest';
    var type = (document.getElementById('artifactsType') || {}).value || '';
    var params = new URLSearchParams({
      page: String(artifactsPage),
      limit: '24',
      sort: sort,
      q: q,
      type: type,
    });
    try {
      var data = await Tomo.api(
        '/api/sessions/' + encodeURIComponent(sessionId) + '/artifacts?' + params.toString()
      );
      grid.innerHTML = '';
      var files = (data && data.files) || [];
      if (!files.length) {
        if (empty) {
          empty.classList.remove('hidden');
          empty.textContent = 'No artifacts in this session yet. The agent can save with save_artifact.';
        }
        if (pager) pager.textContent = '';
        return;
      }
      if (empty) empty.classList.add('hidden');
      files.forEach(function (f) {
        var card = document.createElement('div');
        card.className = 'artifact-card';
        var url = f.url || ('/api/sessions/' + encodeURIComponent(sessionId) + '/artifacts/' + encodeURIComponent(f.filename));
        var isImg = f.category === 'image';
        card.innerHTML =
          (isImg
            ? '<button type="button" class="artifact-thumb artifact-open" data-url="' + url.replace(/"/g, '&quot;') + '" data-filename="' + f.filename.replace(/"/g, '&quot;') + '" data-category="image"><img src="' + url + '" alt=""></button>'
            : '<button type="button" class="artifact-thumb artifact-thumb--file artifact-open" data-url="' + url.replace(/"/g, '&quot;') + '" data-filename="' + f.filename.replace(/"/g, '&quot;') + '" data-category="' + (f.category || 'data') + '">' + (f.category || 'file') + '</button>') +
          '<div class="artifact-meta">' +
          '<button type="button" class="artifact-name artifact-open" data-url="' + url.replace(/"/g, '&quot;') + '" data-filename="' + f.filename.replace(/"/g, '&quot;') + '" data-category="' + (f.category || 'data') + '">' + f.filename + '</button>' +
          '<div class="faint">' + formatBytes(f.size) + '</div>' +
          '<div class="artifact-card-actions">' +
          '<button type="button" class="btn sm artifact-open" data-url="' + url.replace(/"/g, '&quot;') + '" data-filename="' + f.filename.replace(/"/g, '&quot;') + '" data-category="' + (f.category || 'data') + '">Preview</button>' +
          '<button type="button" class="btn sm danger artifact-del" data-filename="' + f.filename.replace(/"/g, '&quot;') + '">Delete</button>' +
          '</div></div>';
        grid.appendChild(card);
      });
      grid.querySelectorAll('.artifact-open').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          if (!window.TomoArtifacts) {
            window.open(btn.dataset.url, '_blank');
            return;
          }
          TomoArtifacts.openPreview({
            url: btn.dataset.url,
            filename: btn.dataset.filename,
            category: btn.dataset.category,
          });
        });
      });
      grid.querySelectorAll('.artifact-del').forEach(function (btn) {
        btn.addEventListener('click', async function () {
          var name = btn.dataset.filename;
          if (!name || !confirm('Delete ' + name + '?')) return;
          try {
            await Tomo.api(
              '/api/sessions/' + encodeURIComponent(sessionId) + '/artifacts/' + encodeURIComponent(name),
              { method: 'DELETE' }
            );
            loadArtifacts(artifactsPage);
            Tomo.toast('Deleted', 'ok');
          } catch (e) {
            Tomo.toast((e && e.message) || 'Delete failed', 'err');
          }
        });
      });
      if (pager) {
        var total = data.total || 0;
        var pages = data.pages || 0;
        pager.textContent = total + ' file(s) in this session' + (pages > 1 ? ' · page ' + artifactsPage + '/' + pages : '');
        if (pages > 1) {
          var prev = document.createElement('button');
          prev.className = 'btn sm';
          prev.textContent = 'Prev';
          prev.disabled = artifactsPage <= 1;
          prev.onclick = function () { loadArtifacts(artifactsPage - 1); };
          var next = document.createElement('button');
          next.className = 'btn sm';
          next.textContent = 'Next';
          next.disabled = artifactsPage >= pages;
          next.onclick = function () { loadArtifacts(artifactsPage + 1); };
          pager.appendChild(document.createTextNode(' '));
          pager.appendChild(prev);
          pager.appendChild(document.createTextNode(' '));
          pager.appendChild(next);
        }
      }
    } catch (e) {
      Tomo.toast((e && e.message) || 'Could not load artifacts', 'err');
    }
  }
  ['artifactsSearch', 'artifactsSort', 'artifactsType'].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(id === 'artifactsSearch' ? 'input' : 'change', function () {
      loadArtifacts(1);
    });
  });
  var refreshBtn = document.getElementById('artifactsRefresh');
  if (refreshBtn) refreshBtn.addEventListener('click', function () { loadArtifacts(artifactsPage); });

  function syncWorkplaceUi() {
    var scopeEl = document.getElementById('cfgWorkplaceScope');
    var scope = scopeEl ? scopeEl.value : 'single';
    var singleRow = document.getElementById('cfgWorkplaceSingleRow');
    var listRow = document.getElementById('cfgWorkplaceListRow');
    var hintRow = document.getElementById('cfgWorkplaceScopeHint');
    var hintText = document.getElementById('cfgWorkplaceScopeHintText');
    if (singleRow) singleRow.style.display = (scope === 'single' || scope === 'list') ? '' : 'none';
    if (listRow) listRow.style.display = scope === 'list' ? '' : 'none';
    if (hintRow) {
      if (scope === 'all_tunnels') {
        hintRow.style.display = '';
        if (hintText) hintText.textContent = 'This agent can use every connected tunnel workplace. Mention a host in chat (e.g. “@ops check disk aio-serv”) to pick one.';
      } else if (scope === 'all') {
        hintRow.style.display = '';
        if (hintText) hintText.textContent = 'This agent can use every workplace (local, SSH, tunnel). Mention a name/host to select one per turn.';
      } else {
        hintRow.style.display = 'none';
      }
    }
  }
  var scopeSel = document.getElementById('cfgWorkplaceScope');
  if (scopeSel) {
    scopeSel.addEventListener('change', syncWorkplaceUi);
    syncWorkplaceUi();
  }
  var cfgSave = document.getElementById('cfgSave');
  if (cfgSave) {
    cfgSave.addEventListener('click', async function () {
      var panel = document.getElementById('panel-config');
      var agentId = panel ? panel.dataset.agentId : '';
      var scope = (document.getElementById('cfgWorkplaceScope') || { value: 'single' }).value;
      var workplaceId = (document.getElementById('cfgWorkplace') || { value: '' }).value;
      var workplaceIds = [];
      if (scope === 'list') {
        document.querySelectorAll('#cfgWorkplaceList input[type="checkbox"]:checked').forEach(function (cb) {
          workplaceIds.push(cb.value);
        });
        if (!workplaceId && workplaceIds.length) workplaceId = workplaceIds[0];
      } else if (scope === 'single') {
        if (workplaceId) workplaceIds = [workplaceId];
      } else {
        workplaceId = '';
        workplaceIds = [];
      }
      var body = {
        name: document.getElementById('cfgName').value.trim(),
        role: document.getElementById('cfgRole').value.trim(),
        model_id: document.getElementById('cfgModel').value,
        workplace_id: workplaceId,
        workplace_ids: workplaceIds,
        workplace_scope: scope,
        description: document.getElementById('cfgDesc').value.trim(),
        artifacts_enabled: !!(document.getElementById('cfgArtifacts') && document.getElementById('cfgArtifacts').checked),
      };
      try {
        await Tomo.api('/api/agents/' + encodeURIComponent(agentId), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        Tomo.toast('Configuration saved', 'ok');
        // Artifacts tab visibility depends on the flag — reload for consistency.
        if (typeof body.artifacts_enabled === 'boolean') {
          setTimeout(function () { location.reload(); }, 400);
        }
      } catch (e) { Tomo.toast((e && e.message) || 'Could not save', 'err'); }
    });
  }
  var toolsSave = document.getElementById('toolsSave');
  if (toolsSave) {
    toolsSave.addEventListener('click', async function () {
      var panel = document.getElementById('panel-tools');
      var agentId = panel ? panel.dataset.agentId : '';
      var enabled = {};
      panel.querySelectorAll('.tool-row[data-tool-id]').forEach(function (row) {
        var id = row.dataset.toolId;
        var input = row.querySelector('input[type="checkbox"]');
        enabled[id] = !!(input && input.checked);
      });
      try {
        await Tomo.api('/api/agents/' + encodeURIComponent(agentId) + '/tools', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: enabled }),
        });
        Tomo.toast('Tools saved', 'ok');
      } catch (e) { Tomo.toast((e && e.message) || 'Could not save tools', 'err'); }
    });
  }
  var skillsSave = document.getElementById('skillsSave');
  if (skillsSave) {
    skillsSave.addEventListener('click', async function () {
      var panel = document.getElementById('panel-skills');
      var agentId = panel ? panel.dataset.agentId : '';
      var skillIds = [];
      panel.querySelectorAll('.skill-row[data-skill-id]').forEach(function (row) {
        var input = row.querySelector('input[type="checkbox"]');
        if (input && input.checked) skillIds.push(row.dataset.skillId);
      });
      try {
        await Tomo.api('/api/agents/' + encodeURIComponent(agentId) + '/skills', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill_ids: skillIds }),
        });
        Tomo.toast('Skills saved', 'ok');
      } catch (e) { Tomo.toast((e && e.message) || 'Could not save skills', 'err'); }
    });
  }
  var wrap = document.querySelector('.chat-wrap');
  // chat.js auto-inits + rehydrates HITL / mid-turn resume on load.
  if (wrap && window.TomoChat) TomoChat.init(wrap);
})();

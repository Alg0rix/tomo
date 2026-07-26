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
    });
  });
  var cfgSave = document.getElementById('cfgSave');
  if (cfgSave) {
    cfgSave.addEventListener('click', async function () {
      var panel = document.getElementById('panel-config');
      var agentId = panel ? panel.dataset.agentId : '';
      var body = {
        name: document.getElementById('cfgName').value.trim(),
        role: document.getElementById('cfgRole').value.trim(),
        model_id: document.getElementById('cfgModel').value,
        workplace_id: (document.getElementById('cfgWorkplace') || { value: '' }).value,
        description: document.getElementById('cfgDesc').value.trim(),
      };
      try {
        await Tomo.api('/api/agents/' + encodeURIComponent(agentId), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        Tomo.toast('Configuration saved', 'ok');
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
  if (wrap && window.TomoChat) TomoChat.init(wrap);
})();

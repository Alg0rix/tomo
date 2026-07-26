/* agent_detail.js — studio tab switching */
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
  var wrap = document.querySelector('.chat-wrap');
  if (wrap && window.TomoChat) TomoChat.init(wrap);
})();

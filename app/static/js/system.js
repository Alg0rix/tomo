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

  function applyLlmStatus(data) {
    var status = document.getElementById('llmKeyStatus');
    var keyEl = document.getElementById('setLlmApiKey');
    var baseEl = document.getElementById('setLlmBaseUrl');
    var modelEl = document.getElementById('setLlmModel');
    if (!data) return;
    if (baseEl && data.llm_base_url) baseEl.value = data.llm_base_url;
    if (modelEl && data.llm_model) modelEl.value = data.llm_model;
    if (keyEl) keyEl.value = '';
    if (status) {
      if (data.llm_api_key_set) {
        status.innerHTML = 'On file: <span class="mono">' +
          Tomo.escapeHtml(data.llm_api_key || '••••') +
          '</span> — leave blank to keep';
      } else {
        status.textContent = 'Not set — required for chat';
      }
    }
  }

  var saveModels = document.getElementById('saveModels');
  if (saveModels) {
    saveModels.addEventListener('click', async function () {
      var base = (document.getElementById('setLlmBaseUrl').value || '').trim();
      var model = (document.getElementById('setLlmModel').value || '').trim();
      if (!base || !model) {
        Tomo.toast('Base URL and model are required', 'err');
        return;
      }
      var payload = { llm_base_url: base, llm_model: model };
      var keyEl = document.getElementById('setLlmApiKey');
      var key = (keyEl && keyEl.value) ? keyEl.value.trim() : '';
      // Never persist masked placeholders or bullet-only autofill junk.
      if (key && key.indexOf('•') === -1) payload.llm_api_key = key;
      try {
        var data = await Tomo.api('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!data) return;
        applyLlmStatus(data);
        Tomo.toast('LLM settings saved', 'ok');
      } catch (e) {
        Tomo.toast((e && e.message) || 'Could not save', 'err');
      }
    });
  }
})();

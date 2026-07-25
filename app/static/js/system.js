/* system.js — settings SPA hash navigation */
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
        await Tomo.api('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            default_model: document.getElementById('setDefaultModel').value,
            max_tool_iterations: parseInt(document.getElementById('setMaxIter').value, 10),
            learning_enabled: document.getElementById('setLearning').checked,
          }),
        });
        Tomo.toast('Settings saved', 'ok');
      } catch (e) {
        Tomo.toast('Could not save', 'err');
      }
    });
  }
})();

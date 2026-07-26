/* setup.js — first-run wizard */
(function () {
  "use strict";

  var step = 1;
  function go(n) {
    step = n;
    [1, 2, 3].forEach(function (i) {
      var el = document.getElementById('step' + i);
      if (el) el.style.display = i === n ? 'block' : 'none';
      var bar = document.querySelector('.setup-step[data-step="' + i + '"]');
      if (bar) {
        bar.classList.toggle('active', i === n);
        bar.classList.toggle('done', i < n);
      }
    });
  }

  document.getElementById('setupNext1').addEventListener('click', function () { go(2); });
  document.getElementById('setupBack2').addEventListener('click', function () { go(1); });
  document.getElementById('setupNext2').addEventListener('click', function () { go(3); });
  document.getElementById('setupBack3').addEventListener('click', function () { go(2); });

  document.getElementById('setupFinish').addEventListener('click', async function () {
    try {
      await Tomo.api('/api/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: document.getElementById('setupBaseUrl').value.trim(),
          api_key: document.getElementById('setupApiKey').value.trim(),
          model: document.getElementById('setupModel').value.trim(),
        }),
      });
      window.location.href = '/login';
    } catch (e) {
      Tomo.toast((e && e.message) || 'Setup failed', 'err');
    }
  });
})();

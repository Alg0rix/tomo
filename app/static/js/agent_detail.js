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
  var wrap = document.querySelector('.chat-wrap');
  if (wrap && window.TomoChat) TomoChat.init(wrap);
})();

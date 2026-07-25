/* rail.js — persistent agent-swarm sidebar. Fetches + renders agent chips. */
(function () {
  "use strict";
  async function renderRail() {
    const list = document.getElementById('railList');
    if (!list) return;
    try {
      const data = await Tomo.api('/api/dashboard/sidebar');
      if (!data) return;
      const agents = data.agents || [];
      const m = location.pathname.match(/^\/agents\/([^/]+)/);
      const agentParam = new URLSearchParams(location.search).get('agent');
      const cur = m ? decodeURIComponent(m[1]) : (agentParam || null);
      list.innerHTML = agents.map(function (a) {
        const sel = cur === a.id ? ' selected' : '';
        const dis = a.enabled ? '' : ' data-disabled';
        const dot = a.busy ? 'busy' : (a.enabled ? 'idle' : 'off');
        const bg = Tomo.avatarColor(a.id);
        return '<a class="agent-chip' + sel + '"' + dis + ' href="/sessions?agent=' + encodeURIComponent(a.id) + '" title="' + Tomo.escapeHtml(a.name) + '">' +
          '<div class="av" style="background:' + bg + '">' + Tomo.escapeHtml((a.name || a.id).slice(0, 1).toUpperCase()) + '</div>' +
          '<span class="dot ' + dot + '"></span></a>';
      }).join('');
    } catch (e) { /* silent */ }
  }
  renderRail();
  // refresh busy states periodically
  setInterval(renderRail, 20000);
  window.Tomo = window.Tomo || {}; window.Tomo.renderRail = renderRail;
})();

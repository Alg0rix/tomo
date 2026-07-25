/* dashboard.js — loads /api/dashboard/data and populates the dashboard. */
(function () {
  "use strict";
  function esc(s) { return Tomo.escapeHtml(s); }

  async function load() {
    let d;
    try { d = await Tomo.api('/api/dashboard/data'); } catch (e) { return; }
    if (!d) return;
    const s = d.stats || {};
    set('s-agents', s.enabled_agent_count, s.agent_count, 'agents');
    set('s-sessions', s.session_count, null, 'sessions');
    set('s-tools', s.tool_count, null, 'tools');
    set('s-skills', s.skill_count, null, 'skills');
    var wp = (d.workplaces || []).length;
    set('s-workplaces', wp, null, 'connected');
    renderAgents(d.recent_agents || []);
    renderSessions(d.recent_sessions || []);
  }
  function set(id, v, total, unit) {
    const el = document.getElementById(id); if (!el) return;
    el.innerHTML = v + (total != null ? '<span class="unit">/' + total + ' ' + unit + '</span>' : (unit ? '<span class="unit"> ' + unit + '</span>' : ''));
  }
  function renderAgents(agents) {
    const box = document.getElementById('recentAgents'); if (!box) return;
    if (!agents.length) { box.innerHTML = '<div class="empty">No agents yet</div>'; return; }
    box.innerHTML = agents.map(function (a) {
      const badge = a.busy ? '<span class="badge amber"><span class="pulse"></span>busy</span>' : (a.enabled ? '<span class="badge ok"><span class="pulse"></span>online</span>' : '<span class="badge muted">off</span>');
      return '<a class="row" href="/agents/' + encodeURIComponent(a.id) + '"><div class="avatar" style="background:' + Tomo.avatarColor(a.id) + '">' + esc((a.name || a.id).slice(0, 1).toUpperCase()) + '</div><div class="meta"><div class="title">' + esc(a.name) + '</div><div class="desc">' + esc(Tomo.truncate(a.description || '—', 52)) + '</div></div>' + badge + '</a>';
    }).join('');
  }
  function renderSessions(sessions) {
    const box = document.getElementById('recentSessions'); if (!box) return;
    if (!sessions.length) { box.innerHTML = '<div class="empty">No sessions yet</div>'; return; }
    box.innerHTML = sessions.map(function (s) {
      return '<a class="row" href="/sessions?s=' + encodeURIComponent(s.id) + '"><div class="meta"><div class="title">' + esc(s.title) + '</div><div class="desc">' + esc(s.agent_id) + ' · ' + s.message_count + ' msgs</div></div><span class="faint mono" style="font-size:11px">' + esc(s.id) + '</span></a>';
    }).join('');
  }
  load();
})();

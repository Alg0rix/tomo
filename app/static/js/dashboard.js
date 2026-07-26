/* dashboard.js — chat-home composer + overview stats. */
(function () {
  "use strict";
  function esc(s) { return Tomo.escapeHtml(s); }

  const form = document.getElementById('homeChatForm');
  const input = document.getElementById('homeChatInput');
  const sendBtn = document.getElementById('homeChatSend');
  const coordNameEl = document.getElementById('homeCoordName');
  const recentHome = document.getElementById('homeRecentChats');
  let coordinatorId = null;

  function resizeHomeInput() {
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }

  function syncSend() {
    // Require a message — empty composer must not create a persisted session.
    if (!sendBtn) return;
    sendBtn.disabled = !input || !input.value.trim() || sendBtn.dataset.busy === '1';
  }

  function startHomeChat(message) {
    const text = (message || '').trim();
    if (!text || !sendBtn) return;
    sendBtn.dataset.busy = '1';
    syncSend();
    // Full swarm by default (not coordinator-only) so delegate works immediately.
    const p = new URLSearchParams();
    p.set('swarm', '1');
    p.set('q', text);
    window.location.href = '/sessions?' + p.toString();
  }

  if (form && input && sendBtn) {
    input.addEventListener('input', function () {
      resizeHomeInput();
      syncSend();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!sendBtn.disabled) form.requestSubmit();
      }
    });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      const text = input.value.trim();
      startHomeChat(text);
    });
    resizeHomeInput();
    syncSend();
  }

  function renderHomeRecent(sessions) {
    if (!recentHome) return;
    if (!sessions.length) {
      recentHome.innerHTML = '<div class="empty">No chats yet — send from the left</div>';
      return;
    }
    recentHome.innerHTML = sessions.slice(0, 6).map(function (s) {
      return '<a class="chat-home-recent-row" href="/sessions?s=' + encodeURIComponent(s.id) + '">' +
        '<div class="meta"><div class="title">' + esc(s.title || 'Conversation') + '</div>' +
        '<div class="desc">' + esc(s.agent_id || '') + ' · ' + esc(String(s.message_count || 0)) + ' msgs</div></div>' +
        '<span class="faint mono ts">' + esc(Tomo.ts ? Tomo.ts(s.updated_at) : '') + '</span></a>';
    }).join('');
  }

  async function load() {
    let d;
    try { d = await Tomo.api('/api/dashboard/data'); } catch (e) { return; }
    if (!d) return;
    if (d.coordinator && coordNameEl) {
      coordinatorId = d.coordinator.id || null;
      coordNameEl.textContent = d.coordinator.name || d.coordinator.id;
      if (input) input.placeholder = 'Message ' + (d.coordinator.name || 'Tomo') + '…';
    }
    renderHomeRecent(d.recent_sessions || []);
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

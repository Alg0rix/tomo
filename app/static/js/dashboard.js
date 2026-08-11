/* dashboard.js — chat-home composer + dashboard overview. */
(function () {
  "use strict";
  function esc(s) { return Tomo.escapeHtml(s); }

  const form = document.getElementById('homeChatForm');
  const input = document.getElementById('homeChatInput');
  const sendBtn = document.getElementById('homeChatSend');
  const coordNameEl = document.getElementById('homeCoordName');
  const recentHome = document.getElementById('homeRecentChats');
  const promptChips = document.querySelectorAll('.prompt-chip');
  const agentRosterCount = document.getElementById('agentRosterCount');
  const scheduleCount = document.getElementById('scheduleCount');
  const schedulesHome = document.getElementById('dashboardSchedules');
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
    // Always pass wp (may be empty = Tomo work dir / no folder).
    var wpSel = document.getElementById('homeWorkplace');
    var wp = wpSel ? (wpSel.value || '') : '';
    p.set('wp', wp);
    window.location.href = '/sessions?' + p.toString();
  }

  function fillHomeWorkplaceSelect(list, selectedId) {
    var sel = document.getElementById('homeWorkplace');
    if (!sel) return;
    var keep = selectedId != null ? selectedId : (sel.value || '');
    var opts = ['<option value="">Tomo work dir (~/tomo/&lt;agent&gt;)</option>'];
    var sorted = (list || []).slice().sort(function (a, b) {
      var ka = a.kind === 'local' ? 0 : 1;
      var kb = b.kind === 'local' ? 0 : 1;
      if (ka !== kb) return ka - kb;
      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
    sorted.forEach(function (w) {
      var id = w.id || '';
      var kind = w.kind || '?';
      var name = w.name || id;
      var path = '';
      if (kind === 'local') path = (w.root_path || '').trim();
      else if (kind === 'ssh') {
        path = ((w.ssh_user || '') + '@' + (w.ssh_host || '')).replace(/^@/, '');
        if (w.root_path) path += ' · ' + w.root_path;
      } else if (kind === 'tunnel') {
        path = (w.connector_hostname || w.host_detail || '') +
          (w.online ? ' · online' : ' · offline');
      }
      opts.push(
        '<option value="' + esc(id) + '" title="' + esc(path || name) + '">' +
        esc(name) + ' · ' + esc(kind) + (path ? (' · ' + esc(path)) : '') +
        '</option>'
      );
    });
    sel.innerHTML = opts.join('');
    // Keep selection when refreshing after browse; default empty on first fill.
    if (keep && sorted.some(function (w) { return w.id === keep; })) {
      sel.value = keep;
    } else if (selectedId != null) {
      sel.value = selectedId || '';
    } else {
      sel.value = '';
    }
  }

  var homeBrowse = document.getElementById('homeBrowseFolder');
  if (homeBrowse) {
    homeBrowse.addEventListener('click', function () {
      if (!Tomo.pickLocalFolder) {
        Tomo.toast('Folder picker not loaded', 'err');
        return;
      }
      Tomo.pickLocalFolder({ title: 'Open folder for new chat' })
        .then(async function (res) {
          var list = [];
          try {
            var wpAll = await Tomo.api('/api/workplaces');
            list = (wpAll && wpAll.workplaces) || [];
          } catch (e) {}
          fillHomeWorkplaceSelect(list, res.workplace_id);
          Tomo.toast(
            (res.created ? 'Registered ' : 'Using ') + (res.path || res.workplace_id),
            'ok'
          );
        })
        .catch(function (err) {
          if (err && err.message === 'cancelled') return;
          Tomo.toast((err && err.message) || 'Browse failed', 'err');
        });
    });
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

  promptChips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      if (!input) return;
      input.value = chip.dataset.prompt || '';
      resizeHomeInput();
      syncSend();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
  });

  function renderHomeRecent(sessions) {
    if (!recentHome) return;
    if (!sessions.length) {
      recentHome.innerHTML = '<div class="dashboard-empty-state dashboard-empty-state-compact"><div><strong>No chats yet</strong><p>Start with the composer or open chat history.</p></div><a class="btn ghost sm" href="/sessions?swarm=1">Open chat ↗</a></div>';
      return;
    }
    recentHome.innerHTML = sessions.slice(0, 6).map(function (s) {
      var title = s.title || 'Conversation';
      var kind = s.is_swarm ? 'Swarm' : (s.agent_id || 'Tomo');
      var marker = String(kind).slice(0, 1).toUpperCase();
      return '<a class="chat-home-recent-row" role="listitem" aria-label="Open ' + esc(title) + '" href="/sessions?s=' + encodeURIComponent(s.id) + '">' +
        '<span class="chat-home-recent-mark' + (s.is_swarm ? ' swarm' : '') + '" aria-hidden="true">' + esc(marker) + '</span>' +
        '<div class="meta"><div class="title">' + esc(title) + '</div>' +
        '<div class="desc"><span class="chat-home-recent-kind">' + esc(kind) + '</span><span>' + esc(String(s.message_count || 0)) + ' msgs</span></div></div>' +
        '<span class="chat-home-recent-arrow" aria-hidden="true">↗</span>' +
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
    // Full workplace list for folder picker (dashboard snapshot is truncated).
    try {
      var wpAll = await Tomo.api('/api/workplaces');
      fillHomeWorkplaceSelect((wpAll && wpAll.workplaces) || d.workplaces || []);
    } catch (eWp) {
      fillHomeWorkplaceSelect(d.workplaces || []);
    }
    const s = d.stats || {};
    set('s-agents', s.enabled_agent_count, s.agent_count, null);
    set('s-sessions', s.session_count, null, null);
    set('s-tools', s.tool_count, null, null);
    set('s-skills', s.skill_count, null, null);
    var wp = s.workplace_count != null ? s.workplace_count : (d.workplaces || []).length;
    set('s-workplaces', wp, null, 'connected');
    renderAgents(d.recent_agents || []);
    renderSchedules(d.schedules || []);
  }
  function set(id, v, total, unit) {
    const el = document.getElementById(id); if (!el) return;
    el.innerHTML = v + (total != null ? '<span class="unit">/' + total + (unit ? ' ' + unit : '') + '</span>' : (unit ? '<span class="unit"> ' + unit + '</span>' : ''));
  }
  function renderAgents(agents) {
    const box = document.getElementById('recentAgents'); if (!box) return;
    if (agentRosterCount) agentRosterCount.textContent = agents.length ? agents.length + ' shown' : '';
    if (!agents.length) {
      box.innerHTML = '<div class="dashboard-empty-state"><div><strong>No agents yet</strong><p>Add a specialist to make the swarm more useful.</p></div><a class="btn ghost sm" href="/agents">Add agent ↗</a></div>';
      return;
    }
    box.innerHTML = agents.map(function (a) {
      const badge = a.busy ? '<span class="badge amber"><span class="pulse"></span>busy</span>' : (a.enabled ? '<span class="badge ok"><span class="pulse"></span>online</span>' : '<span class="badge muted">off</span>');
      const role = a.role || (a.enabled ? 'Available specialist' : 'Disabled');
      return '<a class="row dashboard-agent-row" role="listitem" href="/agents/' + encodeURIComponent(a.id) + '"><div class="avatar" style="background:' + Tomo.avatarColor(a.id) + '">' + esc((a.name || a.id).slice(0, 1).toUpperCase()) + '</div><div class="meta"><div class="title">' + esc(a.name) + '<span class="agent-role">' + esc(role) + '</span></div><div class="desc">' + esc(Tomo.truncate(a.description || '—', 68)) + '</div></div><div class="dashboard-agent-end">' + badge + '<span class="dashboard-row-arrow" aria-hidden="true">↗</span></div></a>';
    }).join('');
  }

  function renderSchedules(schedules) {
    if (!schedulesHome) return;
    var active = (schedules || []).filter(function (s) {
      return s.enabled && s.state !== 'completed';
    });
    if (scheduleCount) scheduleCount.textContent = active.length ? active.length + ' active' : 'none active';
    if (!active.length) {
      schedulesHome.innerHTML = '<div class="dashboard-empty-state"><div><strong>No active automations</strong><p>Schedule recurring work when you are ready.</p></div><a class="btn ghost sm" href="/scheduler">Create one ↗</a></div>';
      return;
    }
    schedulesHome.innerHTML = active.slice(0, 4).map(function (s) {
      var when = s.next_run ? (Tomo.ts ? Tomo.ts(s.next_run) : '') : 'Next run not set';
      return '<a class="dashboard-schedule-row" role="listitem" href="/scheduler"><span class="dashboard-schedule-icon" aria-hidden="true">↻</span><span class="meta"><span class="title">' + esc(s.name || 'Scheduled task') + '</span><span class="desc"><span>' + esc(s.agent_id || 'Tomo') + '</span><span>· ' + esc(s.schedule_display || s.cron || 'Recurring run') + '</span></span></span><span class="dashboard-schedule-when">' + esc(when) + '</span></a>';
    }).join('');
  }
  load();
})();

/* sessions.js — swarm chat UI (session sidebar + multi-agent chat room). */
(function () {
  "use strict";

  const listEl = document.getElementById('sessionList');
  const emptyEl = document.getElementById('sessionEmpty');
  const chatWrap = document.getElementById('sessionChat');
  const searchEl = document.getElementById('sessionSearch');
  const modal = document.getElementById('newChatModal');
  const editModal = document.getElementById('editSwarmModal');
  const newBtn = document.getElementById('newChatBtn');
  const newConfirm = document.getElementById('newChatConfirm');
  const editBtn = document.getElementById('editSwarmBtn');
  const editConfirm = document.getElementById('editSwarmConfirm');

  if (!listEl || !chatWrap) return;

  let sessions = [];
  let agents = {};
  let activeId = null;
  let chatHandle = null;

  function esc(s) { return Tomo.escapeHtml(s); }

  function agentColor(id) {
    return (window.Tomo && Tomo.avatarColor) ? Tomo.avatarColor(id) : 'var(--accent)';
  }

  function agentName(id) {
    return (agents[id] && agents[id].name) || id;
  }

  function isSwarmSession(s) {
    if (!s) return false;
    if (s.is_swarm === true) return true;
    if (s.is_swarm === false) return false;
    const ids = s.agent_ids || (s.agent_id ? [s.agent_id] : []);
    return ids.length !== 1;
  }

  function sessionLabel(s) {
    // Never show agent totals ("Ops +1", "3 agents") — swarm is open-ended.
    if (isSwarmSession(s)) return 'swarm';
    const ids = s.agent_ids || (s.agent_id ? [s.agent_id] : []);
    return agentName(ids[0] || s.agent_id) || 'Chat';
  }

  function applyChatHeader(s) {
    const label = sessionLabel(s);
    const title = (s.title || '').trim() || label;
    document.getElementById('chatAgentName').textContent = title;
    document.getElementById('chatSessionMeta').textContent =
      isSwarmSession(s) ? 'swarm · live agents' : (label + ' · solo');
    chatWrap.dataset.agentName = label;
  }

  function applySessionTitle(sessionId, title) {
    const s = sessions.find(function (x) { return x.id === sessionId; });
    if (s) s.title = title;
    if (sessionId === activeId) {
      const cur = sessions.find(function (x) { return x.id === sessionId; });
      if (cur) applyChatHeader(cur);
      else {
        document.getElementById('chatAgentName').textContent = title;
      }
    }
    renderList(searchEl ? searchEl.value : '');
  }

  function params() {
    return new URLSearchParams(location.search);
  }

  function setUrl(sessionId) {
    const p = new URLSearchParams(location.search);
    if (sessionId) p.set('s', sessionId); else p.delete('s');
    p.delete('agent');
    p.delete('swarm');
    p.delete('q');
    const q = p.toString();
    history.replaceState(null, '', q ? ('?' + q) : location.pathname);
  }

  function stripQueryParam(name) {
    const p = new URLSearchParams(location.search);
    if (!p.has(name)) return;
    p.delete(name);
    const q = p.toString();
    history.replaceState(null, '', q ? ('?' + q) : location.pathname);
  }

  function renderAvatars(ids) {
    const el = document.getElementById('chatAvatars');
    if (!el) return;
    // Show a few faces only — no "+N" total (swarm membership is live/open).
    const shown = (ids || []).slice(0, 4);
    el.innerHTML = shown.map(function (id, i) {
      const name = agentName(id);
      return '<div class="avatar swarm-av" style="background:' + agentColor(id) + ';z-index:' + (10 - i) + '" title="' + esc(name) + '">' + esc(name.slice(0, 1).toUpperCase()) + '</div>';
    }).join('');
  }

  function renderList(filter) {
    const q = (filter || '').trim().toLowerCase();
    const rows = sessions.filter(function (s) {
      if (!q) return true;
      const label = sessionLabel(s);
      return (s.title || '').toLowerCase().includes(q) ||
        label.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q);
    });

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">' + (q ? 'No matching sessions' : 'No sessions yet') + '</div>';
      return;
    }

    listEl.innerHTML = rows.map(function (s) {
      const ids = s.agent_ids || (s.agent_id ? [s.agent_id] : []);
      const label = sessionLabel(s);
      const sel = s.id === activeId ? ' selected' : '';
      const swarm = isSwarmSession(s);
      const avatars = ids.slice(0, 3).map(function (id, i) {
        const n = agentName(id);
        return '<span class="avatar xs" style="background:' + agentColor(id) + ';margin-left:' + (i ? '-6px' : '0') + '">' + esc(n.slice(0, 1).toUpperCase()) + '</span>';
      }).join('');
      return '<button type="button" class="session-item' + sel + '" data-id="' + esc(s.id) + '">' +
        '<div class="session-avatars">' + avatars + '</div>' +
        '<div class="meta"><div class="title">' + esc(s.title || 'Conversation') +
        (swarm ? ' <span class="badge accent sm">swarm</span>' : '') + '</div>' +
        '<div class="desc">' + esc(label) + ' · ' + esc(String(s.message_count || 0)) + ' msgs</div></div>' +
        '<span class="faint mono ts">' + esc(Tomo.ts ? Tomo.ts(s.updated_at) : '') + '</span></button>';
    }).join('');

    listEl.querySelectorAll('.session-item').forEach(function (btn) {
      btn.addEventListener('click', function () { selectSession(btn.dataset.id); });
    });
  }

  function renderHistory(entries) {
    const scroll = chatWrap.querySelector('.chat-scroll');
    scroll.innerHTML = '';
    if (!entries.length) {
      scroll.innerHTML = '<div class="chat-empty"><div class="big">Talk to the swarm</div><div>Send a message — the coordinator routes, or @mention a member to hand off.</div></div>';
      return;
    }
    entries.forEach(function (e) {
      if (e.type === 'delegate') {
        const row = document.createElement('div');
        row.className = 'delegate-line';
        row.textContent = e.content || ('Handing off to ' + agentName(e.agent_id));
        scroll.appendChild(row);
        return;
      }
      if (e.type !== 'user' && e.type !== 'final') return;
      if (e.type === 'final' && !(e.content || '').trim()) return;
      const role = e.type === 'user' ? 'user' : 'assistant';
      const who = role === 'user' ? 'You' : agentName(e.agent_id);
      const row = document.createElement('div');
      row.className = 'msg ' + role;
      const avStyle = role === 'assistant' && e.agent_id ? ' style="background:' + agentColor(e.agent_id) + '"' : '';
      const proseClass = role === 'assistant' ? 'bubble-body prose chat-prose' : 'bubble-body';
      row.innerHTML = '<div class="av"' + avStyle + '>' + esc(role === 'user' ? 'You' : who.slice(0, 1).toUpperCase()) + '</div>' +
        '<div class="bubble"><div class="who">' + esc(who) + '</div><div class="' + proseClass + '"></div></div>';
      const body = row.querySelector('.bubble-body');
      if (role === 'assistant' && window.TomoChat && TomoChat.setMarkdown) {
        TomoChat.setMarkdown(body, e.content || '');
      } else if (role === 'assistant' && window.TomoChat && TomoChat.renderMarkdown) {
        body.textContent = e.content || '';
        TomoChat.renderMarkdown(body);
      } else {
        body.textContent = e.content || '';
      }
      scroll.appendChild(row);
    });
  }

  async function selectSession(sessionId, opts) {
    const s = sessions.find(function (x) { return x.id === sessionId; });
    if (!s) return;
    activeId = sessionId;
    setUrl(sessionId);
    renderList(searchEl ? searchEl.value : '');

    // Live enabled agents for swarm @mentions (not a frozen snapshot).
    const ids = isSwarmSession(s)
      ? allEnabledAgentIds()
      : (s.agent_ids || (s.agent_id ? [s.agent_id] : []));
    const label = sessionLabel(s);
    const pending = opts && opts.pendingMessage ? String(opts.pendingMessage).trim() : '';

    emptyEl.style.display = 'none';
    chatWrap.style.display = 'flex';

    chatWrap.dataset.sessionId = sessionId;
    chatWrap.dataset.userId = s.user_id || 'web';
    chatWrap.dataset.agentIds = ids.join(',');
    chatWrap.dataset.agentsJson = agentsJsonFor(ids);
    chatWrap.dataset.agentName = label;
    chatWrap.dataset.chatInit = '0';
    delete chatWrap.dataset.agentId;

    applyChatHeader(s);
    renderAvatars(ids);

    if (chatHandle && chatHandle.destroy) chatHandle.destroy();
    chatHandle = null;

    try {
      const hist = await Tomo.api('/api/sessions/' + encodeURIComponent(sessionId) + '/chat');
      renderHistory(hist.entries || []);
      chatHandle = TomoChat.init(chatWrap);
      if (pending && chatHandle && chatHandle.send) chatHandle.send(pending);
    } catch (e) {
      Tomo.toast('Could not load session', 'err');
    }
  }

  async function refreshSessions() {
    try {
      // Drop leftover never-messaged drafts from older clients / abandoned creates.
      const keep = activeId || '';
      const pruneUrl = '/api/sessions/prune-drafts' + (keep ? ('?keep_id=' + encodeURIComponent(keep)) : '');
      await Tomo.api(pruneUrl, { method: 'POST' }).catch(function () { /* older servers */ });
      const data = await Tomo.api('/api/sessions');
      if (!data) return;
      sessions = data.sessions || [];
      agents = {};
      (data.agents || []).forEach(function (a) { agents[a.id] = a; });
      renderList(searchEl ? searchEl.value : '');
      if (activeId && !sessions.find(function (s) { return s.id === activeId; })) {
        activeId = null;
        chatWrap.style.display = 'none';
        emptyEl.style.display = 'flex';
      }
    } catch (e) {
      listEl.innerHTML = '<div class="empty">Could not load sessions</div>';
    }
  }

  function pickedAgentIds(container) {
    return Array.from(container.querySelectorAll('input[name="agent"]:checked')).map(function (el) { return el.value; });
  }

  function allEnabledAgentIds() {
    return Object.keys(agents).filter(function (id) {
      return agents[id] && agents[id].enabled !== false;
    });
  }

  function agentsJsonFor(ids) {
    return JSON.stringify((ids || []).map(function (id) {
      const a = agents[id] || {};
      return { id: id, name: a.name || id, role: a.role || '', enabled: a.enabled !== false };
    }));
  }

  function openDraft(agentIds, opts) {
    const ids = agentIds.slice();
    const pending = opts && opts.pendingMessage ? String(opts.pendingMessage).trim() : '';
    activeId = null;
    setUrl(null);
    renderList(searchEl ? searchEl.value : '');

    emptyEl.style.display = 'none';
    chatWrap.style.display = 'flex';

    delete chatWrap.dataset.sessionId;
    delete chatWrap.dataset.agentId;
    chatWrap.dataset.pendingAgents = ids.join(',');
    chatWrap.dataset.userId = 'web';
    chatWrap.dataset.agentIds = ids.join(',');
    chatWrap.dataset.agentsJson = agentsJsonFor(ids);
    chatWrap.dataset.chatInit = '0';

    const draft = {
      id: '',
      title: ids.length > 1 ? 'New swarm chat' : 'New conversation',
      agent_ids: ids,
      agent_id: ids[0],
      user_id: 'web',
      message_count: 0,
    };
    applyChatHeader(draft);
    renderAvatars(ids);
    renderHistory([]);

    if (chatHandle && chatHandle.destroy) chatHandle.destroy();
    chatHandle = TomoChat.init(chatWrap);
    if (pending && chatHandle && chatHandle.send) chatHandle.send(pending);
  }

  function startNewChat(agentIds, opts) {
    var ids = agentIds && agentIds.length ? agentIds.slice() : allEnabledAgentIds();
    if (!ids.length) {
      Tomo.toast('No enabled agents', 'err');
      return;
    }
    // Persist only on first message (chat.js ensureSession).
    openDraft(ids, opts);
  }

  function startDefaultSwarm(opts) {
    startNewChat(allEnabledAgentIds(), opts);
  }

  function buildEditList(ids) {
    const container = document.getElementById('editSwarmAgents');
    if (!container) return;
    const selected = new Set(ids || []);
    container.innerHTML = Object.keys(agents).map(function (id) {
      const a = agents[id];
      const checked = selected.has(id) ? ' checked' : '';
      const dis = a.enabled ? '' : ' disabled';
      return '<label class="agent-pick' + (a.enabled ? '' : ' disabled') + '">' +
        '<input type="checkbox" name="agent" value="' + esc(id) + '"' + checked + dis + '>' +
        '<span class="avatar sm" style="background:' + agentColor(id) + '">' + esc(a.name.slice(0, 1)) + '</span>' +
        '<span class="agent-pick-meta"><span class="name">' + esc(a.name) + '</span></span></label>';
    }).join('');
  }

  if (searchEl) searchEl.addEventListener('input', function () { renderList(searchEl.value); });

  chatWrap.addEventListener('tomo:session-title', function (ev) {
    const d = ev.detail || {};
    if (d.title) applySessionTitle(d.session_id || activeId, d.title);
  });
  chatWrap.addEventListener('tomo:session-created', function (ev) {
    const d = ev.detail || {};
    if (!d.session_id) return;
    activeId = d.session_id;
    setUrl(d.session_id);
    refreshSessions().then(function () {
      const cur = sessions.find(function (x) { return x.id === activeId; });
      if (cur) applyChatHeader(cur);
    });
  });
  chatWrap.addEventListener('tomo:chat-done', function () {
    refreshSessions().then(function () {
      const cur = sessions.find(function (x) { return x.id === activeId; });
      if (cur) applyChatHeader(cur);
    });
  });
  chatWrap.addEventListener('tomo:chat-cleared', function () {
    renderHistory([]);
    refreshSessions();
  });

  function bindModal(m, closeAttr) {
    if (!m) return;
    m.querySelectorAll('[data-close="' + closeAttr + '"]').forEach(function (el) {
      el.addEventListener('click', function () { m.classList.add('hidden'); m.setAttribute('aria-hidden', 'true'); });
    });
  }
  bindModal(modal, '1');
  bindModal(editModal, '2');

  if (newBtn) {
    // Default: full swarm immediately. Hold Alt/Option for the picker (subset/solo).
    newBtn.addEventListener('click', function (e) {
      if (e.altKey && modal) {
        // Pre-check all enabled for the modal.
        document.querySelectorAll('#newChatAgents input[name="agent"]').forEach(function (el) {
          if (!el.disabled) el.checked = true;
        });
        modal.classList.remove('hidden');
        modal.setAttribute('aria-hidden', 'false');
        return;
      }
      startDefaultSwarm();
    });
  }
  if (newConfirm) {
    newConfirm.addEventListener('click', function () {
      const ids = pickedAgentIds(document.getElementById('newChatAgents'));
      modal.classList.add('hidden');
      startNewChat(ids.length ? ids : allEnabledAgentIds());
    });
  }

  if (editBtn && editModal) {
    editBtn.addEventListener('click', function () {
      const s = sessions.find(function (x) { return x.id === activeId; });
      if (!s) return;
      buildEditList(s.agent_ids || [s.agent_id]);
      editModal.classList.remove('hidden');
      editModal.setAttribute('aria-hidden', 'false');
    });
  }
  if (editConfirm) {
    editConfirm.addEventListener('click', async function () {
      const ids = pickedAgentIds(document.getElementById('editSwarmAgents'));
      if (!ids.length) { Tomo.toast('Pick at least one agent', 'err'); return; }
      try {
        await Tomo.api('/api/sessions/' + encodeURIComponent(activeId) + '/agents', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ agent_ids: ids, user_id: 'web' }),
        });
        editModal.classList.add('hidden');
        await refreshSessions();
        if (activeId) selectSession(activeId);
      } catch (e) {
        Tomo.toast('Could not update agents', 'err');
      }
    });
  }

  refreshSessions().then(function () {
    const wanted = params().get('s');
    const agent = params().get('agent');
    const swarm = params().get('swarm');
    const firstMessage = params().get('q') || '';
    // Strip q before auto-send so refresh cannot resend the home composer message.
    if (params().has('q')) stripQueryParam('q');
    if (wanted) selectSession(wanted, { pendingMessage: firstMessage });
    else if (swarm === '1' || swarm === 'true') startDefaultSwarm({ pendingMessage: firstMessage });
    else if (agent) startNewChat([agent], { pendingMessage: firstMessage }); // intentional solo
    else if (sessions.length) selectSession(sessions[0].id);
  });
})();

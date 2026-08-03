/* sessions.js — swarm chat UI (session sidebar + multi-agent chat room). */
(function () {
  "use strict";

  const listEl = document.getElementById('sessionList');
  const emptyEl = document.getElementById('sessionEmpty');
  const chatWrap = document.getElementById('sessionChat');
  const searchView = document.getElementById('sessionSearchView');
  const searchInput = document.getElementById('sessionSearchInput');
  const searchResultsEl = document.getElementById('sessionSearchResults');
  const searchLabelEl = document.getElementById('sessionSearchLabel');
  const searchClearBtn = document.getElementById('sessionSearchClear');
  const searchChatsBtn = document.getElementById('searchChatsBtn');
  const modal = document.getElementById('newChatModal');
  const editModal = document.getElementById('editSwarmModal');
  const newBtn = document.getElementById('newChatBtn');
  const newConfirm = document.getElementById('newChatConfirm');
  const editBtn = document.getElementById('editSwarmBtn');
  const editConfirm = document.getElementById('editSwarmConfirm');
  const sessionsPage = document.getElementById('sessionsPage');
  const sidebarCollapseBtn = document.getElementById('sessionsSidebarCollapse');
  const sidebarExpandBtn = document.getElementById('sessionsSidebarExpand');

  if (!listEl || !chatWrap) return;

  var SIDEBAR_KEY = 'tomo-sessions-sidebar';

  function sidebarCollapsed() {
    return !!(sessionsPage && sessionsPage.classList.contains('is-sidebar-collapsed'));
  }

  function setSidebarCollapsed(collapsed) {
    if (!sessionsPage) return;
    sessionsPage.classList.toggle('is-sidebar-collapsed', !!collapsed);
    if (sidebarExpandBtn) sidebarExpandBtn.classList.toggle('hidden', !collapsed);
    if (sidebarCollapseBtn) {
      sidebarCollapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      sidebarCollapseBtn.title = collapsed ? 'Show chat history' : 'Hide chat history';
      sidebarCollapseBtn.setAttribute('aria-label', sidebarCollapseBtn.title);
    }
    if (sidebarExpandBtn) {
      sidebarExpandBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'open');
    } catch (e) {}
  }

  // Sync reopen button with any anti-flash class set in the template.
  setSidebarCollapsed(sidebarCollapsed());

  if (sidebarCollapseBtn) {
    sidebarCollapseBtn.addEventListener('click', function () {
      setSidebarCollapsed(true);
    });
  }
  if (sidebarExpandBtn) {
    sidebarExpandBtn.addEventListener('click', function () {
      setSidebarCollapsed(false);
    });
  }

  let sessions = [];
  let agents = {};
  let workplaces = [];
  let activeId = null;
  let chatHandle = null;
  var pollTimer = null;
  var monitorEs = null;
  var lastHistLen = -1;
  var inspectorOpenKey = null;
  var draftWorkplaceId = '';
  var searchMode = false;
  var searchTimer = null;
  var searchReq = 0;
  // Account id from the server-rendered page (login session).
  var loginUserId = chatWrap.dataset.userId || 'web';

  function currentUserId() {
    return loginUserId || 'web';
  }

  function stopHistoryPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (monitorEs) { try { monitorEs.close(); } catch (e) {} monitorEs = null; }
  }

  function refetchHistory(sessionId, cb) {
    // Don't wipe the live stream or inspector while the user is in an active turn.
    if (chatWrap.dataset.liveStream === '1') {
      if (cb) cb([]);
      return;
    }
    Tomo.api('/api/sessions/' + encodeURIComponent(sessionId) + '/chat').then(function (hist) {
      var entries = hist.entries || [];
      if (entries.length === lastHistLen && !inspectorOpenKey) {
        if (cb) cb(entries);
        return;
      }
      lastHistLen = entries.length;
      renderHistory(entries);
      if (!chatHandle) chatHandle = TomoChat.init(chatWrap);
      if (cb) cb(entries);
    }).catch(function () {});
  }

  function startHistoryPoll(sessionId) {
    stopHistoryPoll();
    var lastCount = -1;

    // SSE-driven poll: open a monitor connection to the active turn.
    // Heartbeats and turn.end trigger history re-fetches.
    var url = '/api/sessions/' + encodeURIComponent(sessionId) + '/chat/stream?user_id=' + encodeURIComponent(currentUserId()) + '&after=0';
    try {
      monitorEs = new EventSource(url);
      monitorEs.addEventListener('heartbeat', function () {
        refetchHistory(sessionId, function (entries) {
          if (entries.length !== lastCount) {
            lastCount = entries.length;
          }
        });
      });
      monitorEs.addEventListener('turn.end', function () {
        if (monitorEs) { try { monitorEs.close(); } catch (e) {} monitorEs = null; }
        var statusEl = chatWrap.querySelector('.chat-status');
        if (statusEl) {
          statusEl.className = 'badge ok';
          statusEl.innerHTML = '<span class="pulse"></span>online';
        }
        refetchHistory(sessionId);
      });
      monitorEs.addEventListener('error', function () {
        // EventSource will auto-retry; also keep a timer fallback.
      });
    } catch (e) { /* EventSource not available */ }

    // Timer fallback every 3s (in case SSE fails).
    pollTimer = setInterval(function () {
      var sid = chatWrap.dataset.sessionId;
      if (sid !== sessionId) { stopHistoryPoll(); return; }
      refetchHistory(sessionId, function (entries) {
        var last = entries[entries.length - 1];
        if (last && (last.type === 'final' || last.type === 'error')) {
          stopHistoryPoll();
          var statusEl = chatWrap.querySelector('.chat-status');
          if (statusEl) {
            statusEl.className = 'badge ok';
            statusEl.innerHTML = '<span class="pulse"></span>online';
          }
        }
        if (entries.length !== lastCount) {
          lastCount = entries.length;
        }
      });
    }, 3000);
  }

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

  function workplaceFullPath(w) {
    if (!w) return '';
    var kind = (w.kind || '').toLowerCase();
    if (kind === 'local') {
      return (w.root_path || w.host_detail || w.host || '').trim();
    }
    if (kind === 'ssh') {
      var user = (w.ssh_user || '').trim();
      var host = (w.ssh_host || '').trim();
      var port = w.ssh_port || 22;
      var base = user && host ? (user + '@' + host) : (host || user || '');
      if (port && port !== 22 && host) base += ':' + port;
      var root = (w.root_path || '').trim();
      return root ? (base + ' · ' + root) : base;
    }
    // tunnel
    var hn = (w.connector_hostname || w.host_detail || w.name || '').trim();
    var ip = (w.connector_remote_ip || '').trim();
    if (hn && ip) return hn + ' (' + ip + ')';
    return hn || ip || (w.name || w.id || '');
  }

  function workplaceLabel(wid, opts) {
    if (!wid) return 'Tomo work dir (~/tomo/<agent>)';
    var w = workplaces.find(function (x) { return x.id === wid; });
    if (!w) return wid;
    var kind = w.kind || '?';
    var name = w.name || wid;
    var path = workplaceFullPath(w);
    var full = !!opts && opts.full;
    if (kind === 'tunnel') {
      var state = w.online ? 'online' : 'offline';
      return full
        ? (name + ' · tunnel · ' + state + (path ? ' · ' + path : ''))
        : (name + ' · tunnel · ' + state + (path ? ' · ' + path : ''));
    }
    if (kind === 'local') {
      // Always include full absolute path when known.
      return path ? (name + ' · ' + path) : (name + ' · local');
    }
    if (kind === 'ssh') {
      return path ? (name + ' · ' + path) : (name + ' · ssh');
    }
    return path ? (name + ' · ' + path) : (name + ' · ' + kind);
  }

  function fillWorkplaceSelect(selectEl, selectedId) {
    if (!selectEl) return;
    var sel = selectedId || '';
    var opts = ['<option value="">Tomo work dir (~/tomo/&lt;agent&gt;)</option>'];
    // Prefer local first for chat folder context.
    var sorted = workplaces.slice().sort(function (a, b) {
      var ka = (a.kind === 'local') ? 0 : 1;
      var kb = (b.kind === 'local') ? 0 : 1;
      if (ka !== kb) return ka - kb;
      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
    sorted.forEach(function (w) {
      var id = w.id || '';
      var kind = w.kind || '?';
      var name = w.name || id;
      var path = workplaceFullPath(w);
      var extra = '';
      if (kind === 'tunnel') {
        extra = (w.online ? ' · online' : ' · offline') + (path ? ' · ' + path : '');
      } else if (path) {
        extra = ' · ' + path;
      }
      opts.push(
        '<option value="' + esc(id) + '"' + (id === sel ? ' selected' : '') +
        ' title="' + esc(path || name) + '">' +
        esc(name) + ' · ' + esc(kind) + esc(extra) +
        '</option>'
      );
    });
    selectEl.innerHTML = opts.join('');
    selectEl.value = sel;
  }

  function applyChatHeader(s) {
    const label = sessionLabel(s);
    const title = (s.title || '').trim() || label;
    document.getElementById('chatAgentName').textContent = title;
    var wid = (s && s.workplace_id) || chatWrap.dataset.workplaceId || '';
    document.getElementById('chatSessionMeta').textContent =
      isSwarmSession(s) ? 'swarm · live agents' : (label + ' · solo');
    chatWrap.dataset.agentName = label;
    chatWrap.dataset.workplaceId = wid;
    // Read-only badge — workplace is fixed for the thread; show full path.
    var badge = document.getElementById('chatWorkplaceBadge');
    if (badge) {
      var text = workplaceLabel(wid, { full: true });
      badge.textContent = text;
      badge.title = text;
      badge.className = 'badge sm chat-wp-badge mono ' + (wid ? 'ok' : 'muted');
    }
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
    renderList();
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
    p.delete('search');
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

  function setSearchUrl(on) {
    const p = new URLSearchParams(location.search);
    if (on) p.set('search', '1'); else p.delete('search');
    if (on) p.delete('s');
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

  function renderList() {
    const rows = sessions.slice();

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">No sessions yet</div>';
      return;
    }

    // Group by workplace (local folder context for the thread).
    var groups = {};
    var order = [];
    rows.forEach(function (s) {
      var key = (s.workplace_id || '').trim() || '__none__';
      if (!groups[key]) {
        groups[key] = [];
        order.push(key);
      }
      groups[key].push(s);
    });
    // Local workplaces first, then tunnels, then none.
    order.sort(function (a, b) {
      if (a === '__none__') return 1;
      if (b === '__none__') return -1;
      var wa = workplaces.find(function (w) { return w.id === a; }) || {};
      var wb = workplaces.find(function (w) { return w.id === b; }) || {};
      var ka = wa.kind === 'local' ? 0 : (wa.kind === 'tunnel' ? 1 : 2);
      var kb = wb.kind === 'local' ? 0 : (wb.kind === 'tunnel' ? 1 : 2);
      if (ka !== kb) return ka - kb;
      return workplaceLabel(a === '__none__' ? '' : a)
        .localeCompare(workplaceLabel(b === '__none__' ? '' : b));
    });

    function sessionButton(s) {
      const label = sessionLabel(s);
      const sel = s.id === activeId && !searchMode ? ' selected' : '';
      const swarm = isSwarmSession(s);
      return '<button type="button" class="session-item' + sel + '" data-id="' + esc(s.id) + '">' +
        '<div class="meta"><div class="title">' + esc(s.title || 'Conversation') +
        (swarm ? ' <span class="badge accent sm">swarm</span>' : '') + '</div>' +
        '<div class="desc">' + esc(label) + ' · ' + esc(String(s.message_count || 0)) + ' msgs</div></div>' +
        '<span class="faint mono ts">' + esc(Tomo.ts ? Tomo.ts(s.updated_at) : '') + '</span></button>';
    }

    var html = '';
    order.forEach(function (key) {
      var list = groups[key] || [];
      // Newest first within group.
      list.sort(function (a, b) {
        return (b.updated_at || 0) - (a.updated_at || 0);
      });
      var head = key === '__none__' ? 'Tomo work dir (~/tomo/<agent>)' : workplaceLabel(key, { full: true });
      html += '<div class="session-group">' +
        '<div class="session-group-head mono" title="' + esc(head) + '">' + esc(head) +
        ' <span class="faint">(' + list.length + ')</span></div>' +
        list.map(sessionButton).join('') +
        '</div>';
    });
    listEl.innerHTML = html;

    listEl.querySelectorAll('.session-item').forEach(function (btn) {
      btn.addEventListener('click', function () { selectSession(btn.dataset.id); });
    });
  }

  function searchSnippetForSession(s) {
    var label = sessionLabel(s);
    var wp = workplaceLabel(s.workplace_id || '');
    return label + ' · ' + (s.message_count || 0) + ' msgs' + (wp ? ' · ' + wp : '');
  }

  function renderSearchRows(rows, emptyText) {
    if (!searchResultsEl) return;
    if (!rows.length) {
      searchResultsEl.innerHTML = '<div class="sessions-search-empty">' + esc(emptyText || 'No matching chats') + '</div>';
      return;
    }
    searchResultsEl.innerHTML = rows.map(function (r) {
      return '<button type="button" class="sessions-search-row" data-id="' + esc(r.session_id) + '">' +
        '<div class="sessions-search-row-main">' +
          '<div class="sessions-search-row-title">' + esc(r.title || 'Conversation') + '</div>' +
          '<div class="sessions-search-row-snippet">' + esc(r.snippet || '') + '</div>' +
        '</div>' +
        '<span class="sessions-search-row-date">' + esc(Tomo.ts ? Tomo.ts(r.updated_at) : '') + '</span>' +
      '</button>';
    }).join('');
    searchResultsEl.querySelectorAll('.sessions-search-row').forEach(function (btn) {
      btn.addEventListener('click', function () { selectSession(btn.dataset.id); });
    });
  }

  function showRecentInSearch() {
    if (searchLabelEl) searchLabelEl.textContent = 'Recent';
    var rows = sessions.slice().sort(function (a, b) {
      return (b.updated_at || 0) - (a.updated_at || 0);
    }).slice(0, 40).map(function (s) {
      return {
        session_id: s.id,
        title: s.title || 'Conversation',
        snippet: searchSnippetForSession(s),
        updated_at: s.updated_at || 0,
      };
    });
    renderSearchRows(rows, 'No chats yet');
  }

  function runSessionSearch(query) {
    var q = (query || '').trim();
    if (searchClearBtn) searchClearBtn.classList.toggle('hidden', !q);
    if (!q) {
      showRecentInSearch();
      return;
    }
    if (searchLabelEl) searchLabelEl.textContent = 'Results';
    var req = ++searchReq;
    if (searchResultsEl) {
      searchResultsEl.innerHTML = '<div class="sessions-search-empty">Searching…</div>';
    }
    Tomo.api('/api/sessions/search?q=' + encodeURIComponent(q) + '&limit=40').then(function (data) {
      if (req !== searchReq) return;
      renderSearchRows((data && data.results) || [], 'No matching chats');
    }).catch(function () {
      if (req !== searchReq) return;
      // Fallback: local title filter if API fails.
      var needle = q.toLowerCase();
      var rows = sessions.filter(function (s) {
        return ((s.title || '') + ' ' + (s.id || '')).toLowerCase().indexOf(needle) >= 0;
      }).map(function (s) {
        return {
          session_id: s.id,
          title: s.title || 'Conversation',
          snippet: searchSnippetForSession(s),
          updated_at: s.updated_at || 0,
        };
      });
      renderSearchRows(rows, 'No matching chats');
    });
  }

  function openSearchView() {
    searchMode = true;
    if (emptyEl) emptyEl.style.display = 'none';
    chatWrap.style.display = 'none';
    if (searchView) {
      searchView.style.display = 'flex';
      searchView.hidden = false;
    }
    setSearchUrl(true);
    renderList();
    if (searchChatsBtn) searchChatsBtn.classList.add('active');
    showRecentInSearch();
    if (searchInput) {
      searchInput.focus();
      searchInput.select();
    }
  }

  function closeSearchView() {
    searchMode = false;
    if (searchView) {
      searchView.style.display = 'none';
      searchView.hidden = true;
    }
    if (searchChatsBtn) searchChatsBtn.classList.remove('active');
    if (searchTimer) { clearTimeout(searchTimer); searchTimer = null; }
  }

  function stickChatScrollBottom(scroll) {
    if (window.Tomo && Tomo.stickScrollBottom) Tomo.stickScrollBottom(scroll);
    else if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }

  function renderHistory(entries) {
    const scroll = chatWrap.querySelector('.chat-scroll');
    scroll.innerHTML = '';
    if (window.Tomo && Tomo.clearTodoDock) Tomo.clearTodoDock(chatWrap);
    if (!entries.length) {
      scroll.innerHTML = '<div class="chat-empty"><div class="big">Talk to the swarm</div><div>Send a message — the coordinator routes, or @mention a member to hand off.</div></div>';
      return;
    }

    // ── Per-turn state ──────────────────────────────────────────────
    var turn = null;
    var turnId = 0;
    var delegateCounter = 0;          // unique per delegate call within a turn
    var agentToKey = {};               // agent_id → current buffer key
    var subagentSet = new Set();
    var subagentBuffers = new Map();  // key: turnId + ':' + delegateCounter → buffer
    var turnBuffers = new Map();      // current turn: key → buffer
    var swarmCard = null;
    var detailPanel = null;

    function getBuffer(key) {
      if (!turnBuffers.has(key)) {
        var buf = { events: [], name: '', task: '', status: 'running', row: null, aid: '', turnId: turnId };
        turnBuffers.set(key, buf);
        subagentBuffers.set(key, buf);
      }
      return turnBuffers.get(key);
    }

    function keyForAid(aid) {
      return agentToKey[aid] || aid;
    }

    function startTurn() {
      turnId++;
      turn = document.createElement('div');
      turn.className = 'turn';
      scroll.appendChild(turn);
      swarmCard = null;
      subagentSet = new Set();
      turnBuffers = new Map();
      agentToKey = {};
      delegateCounter = 0;
      var oldPanel = chatWrap.querySelector('.subagent-inspector, .detail-panel');
      if (oldPanel) oldPanel.remove();
      detailPanel = null;
    }

    function ensureSwarmCard() {
      if (swarmCard) return swarmCard;
      swarmCard = document.createElement('div');
      swarmCard.className = 'swarm-card';
      turn.appendChild(swarmCard);
      return swarmCard;
    }

    function addSwarmRow(aid, name, task, idx, total) {
      var card = ensureSwarmCard();
      var row = document.createElement('div');
      row.className = 'swarm-row';
      row.dataset.agentId = aid;
      var color = agentColor(aid);
      var letter = esc((name || aid || '?').slice(0, 1).toUpperCase());
      var idxStr = String(idx || 1).padStart(2, '0');
      var totalStr = String(total || 1).padStart(2, '0');
      row.innerHTML =
        '<div class="av" style="background:' + color + '">' + letter + '</div>' +
        '<div class="swarm-meta">' +
          '<div class="swarm-row-head">' +
            '<span class="name">' + esc(name || aid) + '</span>' +
            '<span class="index">' + idxStr + ' / ' + totalStr + '</span>' +
          '</div>' +
          '<div class="task">' + esc(task || '') + '</div>' +
          '<div class="swarm-progress"><div class="swarm-progress-bar" style="width:0%"></div></div>' +
        '</div>' +
        '<span class="si-open-hint" aria-hidden="true">inspect →</span>';
      row.addEventListener('click', function () { openDetailPanel(row); });
      card.appendChild(row);
      var key = turnId + ':' + delegateCounter;
      var buf = getBuffer(key);
      buf.row = row;
      buf.key = key;
      row._buffer = buf;
      row.dataset.bufferKey = key;
      buf.name = name || aid;
      buf.aid = aid;
      buf.task = task || '';
      return row;
    }

    function markSwarmDone(key, status) {
      var buf = turnBuffers.get(key);
      if (!buf) return;
      buf.status = status === 'error' ? 'error' : 'done';
      if (!buf.row) return;
      buf.row.classList.remove('active');
      buf.row.classList.add(buf.status);
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) bar.style.width = '100%';
    }

    function bumpSwarmProgress(key) {
      var buf = turnBuffers.get(key);
      if (!buf || !buf.row) return;
      buf.row.classList.add('active');
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) {
        var w = parseFloat(bar.style.width) || 0;
        bar.style.width = Math.min(92, w + 7) + '%';
      }
    }

    function bufferEvent(key, kind, data) {
      var buf = turnBuffers.get(key) || getBuffer(key);
      buf.events.push({ kind: kind, data: data });
    }

    function makeToolCollapsible(card) {
      if (window.Tomo && Tomo.wireToolCard) Tomo.wireToolCard(card);
    }

    function toolCallStillRunning(entries, index) {
      // Unpaired tool_call (no later tool_output for same agent) while the
      // turn has not finished — still executing after a mid-turn refresh.
      var e = entries[index];
      if (!e || e.type !== 'tool_call') return false;
      var last = entries[entries.length - 1];
      if (!last || last.type === 'final' || last.type === 'error') return false;
      var aid = e.agent_id || '';
      for (var i = index + 1; i < entries.length; i++) {
        var n = entries[i];
        if (n.type === 'tool_output' && (n.agent_id || '') === aid) return false;
        if (n.type === 'user' || n.type === 'final' || n.type === 'error') return false;
      }
      return true;
    }

    function buildHistoryToolCard(fn, params, running) {
      if (window.Tomo && Tomo.buildToolCard) {
        return Tomo.buildToolCard({ tool: fn || 'tool', args: params || {}, running: !!running });
      }
      var card = document.createElement('div');
      card.className = 'tool' + (running ? ' loading' : ' ok');
      card.innerHTML =
        '<button type="button" class="tool-head">' +
          '<span class="tstatus"></span><span class="tname">' + esc(fn || 'tool') + '</span>' +
          '<span class="targs"></span><span class="tchip"></span><span class="chevron"></span>' +
        '</button><div class="tool-body"><pre class="tres"></pre></div>';
      card._res = card.querySelector('.tres');
      card._chip = card.querySelector('.tchip');
      makeToolCollapsible(card);
      return card;
    }

    function renderEventInDetail(kind, data, body) {
      if (window.Tomo && Tomo.renderInspectorStep) {
        Tomo.renderInspectorStep(body, kind, data);
        return;
      }
    }

    function openDetailPanel(rowOrAid) {
      var buf, aid, name;
      if (rowOrAid && rowOrAid._buffer) {
        buf = rowOrAid._buffer;
        aid = buf.aid || rowOrAid.dataset.agentId || '';
        name = buf.name || aid;
      } else {
        aid = rowOrAid;
        buf = getBuffer(aid);
        name = buf.name || aid;
      }
      var color = agentColor(aid);
      var letter = esc((name || '?').slice(0, 1).toUpperCase());
      var status = buf.status || 'done';
      if (buf.row && buf.row.classList.contains('done')) status = 'done';
      if (buf.row && buf.row.classList.contains('error')) status = 'error';
      if (status === 'running' && buf.events.some(function (e) { return e.kind === 'final'; })) status = 'done';
      inspectorOpenKey = buf.key || keyForAid(aid) || null;

      var panel = chatWrap.querySelector('.subagent-inspector');
      if (!panel) {
        panel = document.createElement('aside');
        panel.className = 'subagent-inspector';
        panel.setAttribute('role', 'complementary');
        panel.setAttribute('aria-label', 'Subagent inspector');
        chatWrap.appendChild(panel);
      }
      detailPanel = panel;
      panel.innerHTML = '';

      var head = document.createElement('div');
      head.className = 'si-head';
      head.innerHTML =
        '<div class="si-agent">' +
          '<div class="av" style="background:' + color + '">' + letter + '</div>' +
          '<div class="si-meta">' +
            '<div class="si-name-row">' +
              '<span class="si-name">' + esc(name) + '</span>' +
              '<span class="si-status ' + esc(status) + '">' + esc(status) + '</span>' +
            '</div>' +
            '<div class="si-id">@' + esc(aid) + '</div>' +
          '</div>' +
        '</div>' +
        '<button class="si-close" type="button" title="Close" aria-label="Close inspector">\u2715</button>';
      panel.appendChild(head);

      var taskEl = document.createElement('div');
      taskEl.className = 'si-task';
      if (buf.task) {
        taskEl.innerHTML = '<span class="si-task-label">Task</span>' + esc(buf.task);
      }
      panel.appendChild(taskEl);

      var bufferList = [];
      turnBuffers.forEach(function (b) { bufferList.push(b); });
      if (bufferList.length > 1) {
        var nameCounts = {};
        bufferList.forEach(function (b) {
          var base = b.name || b.aid || '';
          nameCounts[base] = (nameCounts[base] || 0) + 1;
        });
        var nameSeen = {};
        var switcher = document.createElement('nav');
        switcher.className = 'si-switcher';
        switcher.setAttribute('aria-label', 'Subagents in this turn');
        bufferList.forEach(function (b) {
          var base = b.name || b.aid || '';
          nameSeen[base] = (nameSeen[base] || 0) + 1;
          var pill = document.createElement('button');
          pill.type = 'button';
          pill.className = 'si-pill' + (b === buf ? ' active' : '');
          var st = b.status || 'done';
          var cColor = agentColor(b.aid || '');
          var cLetter = esc((b.name || b.aid || '?').slice(0, 1).toUpperCase());
          var label = base;
          if (nameCounts[base] > 1) label += ' #' + nameSeen[base];
          pill.innerHTML =
            '<span class="av" style="background:' + cColor + '">' + cLetter + '</span>' +
            '<span>' + esc(label) + '</span>' +
            '<span class="dot ' + esc(st) + '"></span>';
          pill.addEventListener('click', function () { if (b.row) openDetailPanel(b.row); });
          switcher.appendChild(pill);
        });
        panel.appendChild(switcher);
      }

      var body = document.createElement('div');
      body.className = 'si-body';
      panel.appendChild(body);

      if (!buf.events.length) {
        body.innerHTML = '<div class="si-empty">No buffered steps for this agent.</div>';
      } else {
        var tl = document.createElement('div');
        tl.className = 'si-timeline';
        body.appendChild(tl);
        buf.events.forEach(function (ev) {
          renderEventInDetail(ev.kind, ev.data, body);
        });
      }

      head.querySelector('.si-close').addEventListener('click', closeDetailPanel);
      chatWrap.querySelectorAll('.swarm-row').forEach(function (r) {
        r.classList.toggle('selected', r === (buf.row || rowOrAid) || r.dataset.agentId === aid);
      });
      requestAnimationFrame(function () { body.scrollTop = body.scrollHeight; });
    }

    function closeDetailPanel() {
      inspectorOpenKey = null;
      chatWrap.querySelectorAll('.swarm-row.selected').forEach(function (r) {
        r.classList.remove('selected');
      });
      var panel = chatWrap.querySelector('.subagent-inspector');
      detailPanel = null;
      if (panel) panel.remove();
    }

    // ── Process entries ─────────────────────────────────────────────
    entries.forEach(function (e, entryIdx) {
      if (e.type === 'user') {
        startTurn();
        var row = document.createElement('div');
        row.className = 'msg user';
        var chips = '';
        var atts = e.attachments || (e.params && e.params.attachments) || [];
        if (atts.length) {
          chips = '<div class="bubble-attachments">' + atts.map(function (a) {
            var sz = a.size != null ? a.size : a.size_bytes;
            var sizeHtml = '';
            if (sz != null) {
              sizeHtml = '<span class="size">' + (sz < 1024 ? sz + 'B' : sz < 1048576 ? (sz / 1024).toFixed(1) + 'KB' : (sz / 1048576).toFixed(1) + 'MB') + '</span>';
            }
            return '<span class="attachment-chip"><span class="name">' + esc(a.name || a.original_name || 'file') + '</span>' + sizeHtml + '</span>';
          }).join('') + '</div>';
        }
        row.innerHTML = '<div class="bubble"><div class="bubble-body"></div>' +
          (window.TomoChat && TomoChat.msgActionsHtml ? TomoChat.msgActionsHtml('user') : '') + '</div>';
        var body = row.querySelector('.bubble-body');
        body.dataset.raw = e.content || '';
        body.textContent = e.content || '';
        if (chips) body.insertAdjacentHTML('beforeend', chips);
        turn.appendChild(row);
        return;
      }

      if (!turn) startTurn();

      if (e.type === 'delegate') {
        var p = e.params || {};
        var aid = e.agent_id || p.to || '';
        var name = p.to_name || agentName(aid);
        var task = p.task || p.reason || '';
        var idx = p.parallel_index || 1;
        var total = p.parallel_total || 1;
        if (aid) subagentSet.add(aid);
        delegateCounter++;
        agentToKey[aid] = turnId + ':' + delegateCounter;
        var buf = getBuffer(agentToKey[aid]);
        buf.name = name; buf.aid = aid; buf.task = task;
        addSwarmRow(aid, name, task, idx, total);
        return;
      }

      if (e.type === 'subagent_start') {
        var p = e.params || {};
        var aid = e.agent_id || '';
        var name = p.name || agentName(aid);
        var task = p.task || '';
        var idx = p.parallel_index || 1;
        var total = p.parallel_total || 1;
        if (aid) subagentSet.add(aid);
        // Reuse buffer from a prior delegate entry — both events are persisted
        // for the same handoff; only create a new slot when none exists yet.
        var key = agentToKey[aid];
        if (!key || !turnBuffers.has(key)) {
          delegateCounter++;
          key = turnId + ':' + delegateCounter;
          agentToKey[aid] = key;
        }
        var buf = getBuffer(key);
        buf.name = name; buf.aid = aid; buf.task = task || buf.task;
        if (!buf.row) addSwarmRow(aid, name, task, idx, total);
        if (buf.row) buf.row.classList.add('active');
        return;
      }

      if (e.type === 'subagent_done') {
        var p = e.params || {};
        markSwarmDone(keyForAid(e.agent_id || ''), p.status || 'ok');
        return;
      }

      if (e.type === 'tool_call') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForAid(aid);
          bufferEvent(key, 'tool', { tool: e.function, args: e.params });
          bumpSwarmProgress(key);
        } else {
          turn.appendChild(buildHistoryToolCard(
            e.function, e.params, toolCallStillRunning(entries, entryIdx)
          ));
        }
        return;
      }

      if (e.type === 'tool_output') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForAid(aid);
          bufferEvent(key, 'tool_result', { result: e.content, error: e.error });
          bumpSwarmProgress(key);
        } else {
          var cards = turn.querySelectorAll('.tool');
          var last = cards[cards.length - 1];
          var resultText = e.content || '';
          if (last) {
            if (window.Tomo && Tomo.finishToolCard) {
              Tomo.finishToolCard(last, resultText, !!e.error);
            } else if (last._res) {
              last._res.textContent = resultText;
              last.classList.remove('loading');
            }
          }
          if (!e.error && window.TomoArtifacts) {
            var parsedArt = TomoArtifacts.parseSaveResult(e.function || '', resultText);
            if (parsedArt) {
              // Inline card only — never auto-open the side panel while
              // replaying history (that re-opens closed panels on every refresh).
              turn.appendChild(TomoArtifacts.buildSavedCard(parsedArt));
            }
          }
        }
        return;
      }

      if (e.type === 'thinking') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForAid(aid);
          bufferEvent(key, 'thinking', { content: e.content });
          bumpSwarmProgress(key);
        }
        return;
      }

      if (e.type === 'final') {
        var aid = e.agent_id || '';
        var text = (e.content || '').trim();
        if (!text || text.indexOf('[Swarm]') === 0) return;
        if (subagentSet.has(aid)) {
          var key = keyForAid(aid);
          bufferEvent(key, 'final', { content: text });
        } else {
          var who = agentName(aid);
          var row = document.createElement('div');
          row.className = 'msg assistant';
          var avStyle = ' style="background:' + agentColor(aid) + '"';
          row.innerHTML = '<div class="av"' + avStyle + '>' + esc(who.slice(0, 1).toUpperCase()) + '</div>' +
            '<div class="bubble"><div class="who">' + esc(who) + '</div><div class="bubble-body prose chat-prose"></div>' +
            (window.TomoChat && TomoChat.msgActionsHtml ? TomoChat.msgActionsHtml('assistant') : '') + '</div>';
          var body = row.querySelector('.bubble-body');
          if (window.TomoChat && TomoChat.setMarkdown) {
            TomoChat.setMarkdown(body, text);
          } else {
            body.dataset.raw = text;
            body.textContent = text;
          }
          turn.appendChild(row);
        }
        return;
      }

      if (e.type === 'error') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          markSwarmDone(keyForAid(aid), 'error');
        } else {
          var row = document.createElement('div');
          row.className = 'msg error';
          row.innerHTML = '<div class="bubble"><div class="bubble-body" style="color:var(--danger)">' + esc(e.content || 'Error') + '</div></div>';
          turn.appendChild(row);
        }
        return;
      }
    });

    if (inspectorOpenKey) {
      var reopen = scroll.querySelector('.swarm-row[data-buffer-key="' + inspectorOpenKey + '"]');
      if (reopen) openDetailPanel(reopen);
    }

    stickChatScrollBottom(scroll);
  }

  async function selectSession(sessionId, opts) {
    const s = sessions.find(function (x) { return x.id === sessionId; });
    if (!s) return;
    closeSearchView();
    activeId = sessionId;
    setUrl(sessionId);
    renderList();

    // Live enabled agents for swarm @mentions (not a frozen snapshot).
    const ids = isSwarmSession(s)
      ? allEnabledAgentIds()
      : (s.agent_ids || (s.agent_id ? [s.agent_id] : []));
    const label = sessionLabel(s);
    const pending = opts && opts.pendingMessage ? String(opts.pendingMessage).trim() : '';

    emptyEl.style.display = 'none';
    chatWrap.style.display = 'flex';

    chatWrap.dataset.sessionId = sessionId;
    chatWrap.dataset.userId = s.user_id || currentUserId();
    chatWrap.dataset.agentIds = ids.join(',');
    chatWrap.dataset.agentsJson = agentsJsonFor(ids);
    chatWrap.dataset.agentName = label;
    chatWrap.dataset.chatInit = '0';
    delete chatWrap.dataset.ctxInit;
    delete chatWrap.dataset.agentId;

    applyChatHeader(s);
    renderAvatars(ids);

    if (chatHandle && chatHandle.destroy) chatHandle.destroy();
    chatHandle = null;
    stopHistoryPoll();
    lastHistLen = -1;
    inspectorOpenKey = null;

    try {
      const hist = await Tomo.api('/api/sessions/' + encodeURIComponent(sessionId) + '/chat');
      renderHistory(hist.entries || []);
      chatHandle = TomoChat.init(chatWrap);
      // init may re-touch markdown; stick again after layout settles
      stickChatScrollBottom(chatWrap.querySelector('.chat-scroll'));

      // Mid-turn refresh: history shows unpaired tools as running; re-attach
      // to the live session stream so status stays busy and results update.
      var entries = hist.entries || [];
      var last = entries[entries.length - 1];
      if (last && last.type !== 'final' && last.type !== 'error' && !pending) {
        var statusEl = chatWrap.querySelector('.chat-status');
        if (statusEl) {
          statusEl.className = 'badge amber';
          statusEl.innerHTML = '<span class="pulse"></span>busy';
        }
        var resumed = chatHandle && chatHandle.resume && chatHandle.resume();
        if (!resumed) startHistoryPoll(sessionId);
      }

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
      try {
        var wpData = await Tomo.api('/api/workplaces');
        workplaces = (wpData && wpData.workplaces) || [];
      } catch (e2) {
        workplaces = [];
      }
      fillWorkplaceSelect(
        document.getElementById('newChatWorkplace'),
        draftWorkplaceId
      );
      if (activeId) {
        var cur = sessions.find(function (s) { return s.id === activeId; });
        if (cur) applyChatHeader(cur);
      }
      renderList();
      if (activeId && !sessions.find(function (s) { return s.id === activeId; })) {
        activeId = null;
        chatWrap.style.display = 'none';
        if (!searchMode) emptyEl.style.display = 'flex';
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
    // Default: no workplace folder → agent Tomo work dir. Only when opts.workplaceId set.
    var wpId = '';
    if (opts && Object.prototype.hasOwnProperty.call(opts, 'workplaceId')) {
      wpId = opts.workplaceId || '';
    }
    draftWorkplaceId = wpId || '';
    activeId = null;
    closeSearchView();
    setUrl(null);
    renderList();

    emptyEl.style.display = 'none';
    chatWrap.style.display = 'flex';

    delete chatWrap.dataset.sessionId;
    delete chatWrap.dataset.agentId;
    chatWrap.dataset.pendingAgents = ids.join(',');
    chatWrap.dataset.userId = currentUserId();
    chatWrap.dataset.agentIds = ids.join(',');
    chatWrap.dataset.agentsJson = agentsJsonFor(ids);
    chatWrap.dataset.workplaceId = draftWorkplaceId;
    chatWrap.dataset.chatInit = '0';
    delete chatWrap.dataset.ctxInit;

    const draft = {
      id: '',
      title: ids.length > 1 ? 'New swarm chat' : 'New conversation',
      agent_ids: ids,
      agent_id: ids[0],
      user_id: currentUserId(),
      message_count: 0,
      workplace_id: draftWorkplaceId,
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

  if (searchChatsBtn) {
    searchChatsBtn.addEventListener('click', function () {
      if (searchMode) {
        // Already open — just focus the field.
        if (searchInput) searchInput.focus();
        return;
      }
      openSearchView();
    });
  }
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        runSessionSearch(searchInput.value);
      }, 180);
    });
    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (searchInput.value) {
          searchInput.value = '';
          runSessionSearch('');
        } else if (activeId) {
          selectSession(activeId);
        } else {
          closeSearchView();
          setUrl(null);
          emptyEl.style.display = 'flex';
          chatWrap.style.display = 'none';
          renderList();
        }
      }
    });
  }
  if (searchClearBtn) {
    searchClearBtn.addEventListener('click', function () {
      if (!searchInput) return;
      searchInput.value = '';
      runSessionSearch('');
      searchInput.focus();
    });
  }

  chatWrap.addEventListener('tomo:turn-start', function () {
    stopHistoryPoll();
  });
  chatWrap.addEventListener('tomo:turn-end', function () {
    var sid = chatWrap.dataset.sessionId;
    if (sid) {
      lastHistLen = -1;
      refetchHistory(sid);
    }
  });
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

  async function refreshWorkplacesAndSelect(selectedId) {
    try {
      var wpData = await Tomo.api('/api/workplaces');
      workplaces = (wpData && wpData.workplaces) || workplaces;
    } catch (e) { /* keep cache */ }
    fillWorkplaceSelect(document.getElementById('newChatWorkplace'), selectedId || '');
    draftWorkplaceId = selectedId || '';
  }

  function openNewChatModal() {
    if (!modal) {
      startDefaultSwarm({ workplaceId: '' });
      return;
    }
    document.querySelectorAll('#newChatAgents input[name="agent"]').forEach(function (el) {
      if (!el.disabled) el.checked = true;
    });
    draftWorkplaceId = '';
    fillWorkplaceSelect(document.getElementById('newChatWorkplace'), '');
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
  }

  var browseBtn = document.getElementById('newChatBrowseFolder');
  if (browseBtn) {
    browseBtn.addEventListener('click', function () {
      if (!Tomo.pickLocalFolder) {
        Tomo.toast('Folder picker not loaded', 'err');
        return;
      }
      Tomo.pickLocalFolder({ title: 'Open folder for this chat' })
        .then(function (res) {
          return refreshWorkplacesAndSelect(res.workplace_id).then(function () {
            Tomo.toast(
              (res.created ? 'Registered ' : 'Using ') +
                (res.path || res.workplace_id),
              'ok'
            );
          });
        })
        .catch(function (err) {
          if (err && err.message === 'cancelled') return;
          Tomo.toast((err && err.message) || 'Browse failed', 'err');
        });
    });
  }

  if (newBtn) {
    // Always open picker so user can choose workplace (default: Tomo work dir).
    newBtn.addEventListener('click', function () {
      openNewChatModal();
    });
  }
  if (newConfirm) {
    newConfirm.addEventListener('click', function () {
      const ids = pickedAgentIds(document.getElementById('newChatAgents'));
      var wpSel = document.getElementById('newChatWorkplace');
      draftWorkplaceId = wpSel ? (wpSel.value || '') : '';
      modal.classList.add('hidden');
      modal.setAttribute('aria-hidden', 'true');
      startNewChat(ids.length ? ids : allEnabledAgentIds(), {
        workplaceId: draftWorkplaceId,
      });
    });
  }

  // Workplace is fixed at chat create — no mid-thread switcher.

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
          body: JSON.stringify({ agent_ids: ids, user_id: currentUserId() }),
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
    const wantSearch = params().get('search') === '1';
    const firstMessage = params().get('q') || '';
    // Dashboard may pass wp= (empty = Tomo work dir).
    var wpParam = params().has('wp') ? (params().get('wp') || '') : null;
    // Strip one-shot query params before auto-send.
    if (params().has('q')) stripQueryParam('q');
    if (params().has('wp')) stripQueryParam('wp');
    if (params().has('swarm')) stripQueryParam('swarm');
    var draftOpts = { pendingMessage: firstMessage };
    if (wpParam !== null) draftOpts.workplaceId = wpParam;
    if (wantSearch) openSearchView();
    else if (wanted) selectSession(wanted, { pendingMessage: firstMessage });
    else if (swarm === '1' || swarm === 'true') startDefaultSwarm(draftOpts);
    else if (agent) startNewChat([agent], draftOpts); // intentional solo
    else if (sessions.length) selectSession(sessions[0].id);
  });
})();

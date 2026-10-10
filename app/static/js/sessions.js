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
  if (!listEl || !chatWrap) return;

  let sessions = [];
  let agents = {};
  let workplaces = [];
  let activeId = null;
  let chatHandle = null;
  const composerDrafts = new Map();
  var selectionSeq = 0;
  var pollTimer = null;
  var monitorEs = null;
  var lastHistLen = -1;
  var historyEntries = [];
  var historyHasMore = false;
  var historyBefore = null;
  var historyLoading = false;
  var historyRequest = 0;
  var historyQueries = [];

  async function refreshQueryRail(sessionId) {
    var selection = selectionSeq;
    try {
      var data = await Tomo.api('/api/sessions/' + encodeURIComponent(sessionId) + '/chat/queries');
      if (selection !== selectionSeq || chatWrap.dataset.sessionId !== sessionId) return;
      historyQueries = (data.queries || []).map(function (entry, index) {
        return { id: 'chat-message-' + entry.message_id, messageId: entry.message_id,
          index: index, prompt: entry.content || '', context: '' };
      });
      // A completed live turn used a temporary query ID. Adopt persisted IDs
      // without rebuilding its messages or moving the scroll position.
      var turns = Array.from(chatWrap.querySelectorAll('.chat-scroll .turn[data-query-id]'));
      var users = historyEntries.filter(function (entry) { return entry.type === 'user'; });
      if (turns.length === users.length && chatWrap.dataset.liveStream !== '1') {
        turns.forEach(function (turn, index) {
          if (users[index].message_id) turn.dataset.queryId = 'chat-message-' + users[index].message_id;
        });
      }
      renderQueryRail(fullQueryRecords(buildQueryRecords(historyEntries)));
      scheduleQueryContexts();
    } catch (_) {}
  }

  function fullQueryRecords(loaded) {
    if (!historyQueries.length) return loaded;
    var byId = new Map(loaded.map(function (record) { return [record.id, record]; }));
    var queryIds = new Set(historyQueries.map(function (record) { return record.id; }));
    return historyQueries.map(function (record) {
      var visible = byId.get(record.id);
      return visible ? Object.assign({}, record, { prompt: visible.prompt, context: visible.context }) : record;
    }).concat(loaded.filter(function (record) {
      return !queryIds.has(record.id);
    }).map(function (record, index) {
      return Object.assign({}, record, { index: historyQueries.length + index });
    }));
  }
  var inspectorOpenKey = null;
  var draftWorkplaceId = '';
  var searchMode = false;
  var searchTimer = null;
  var searchReq = 0;
  // Account id from the server-rendered page (login session).
  var loginUserId = chatWrap.dataset.userId || 'web';

  // Stored swarm runs attach to the history turns that ran them.
  var swarmHydrateSeq = 0;
  function cancelSwarmHydrate() { ++swarmHydrateSeq; }
  async function hydrateSwarmRuns(sessionId) {
    if (!sessionId || !window.TomoSwarm || !chatWrap.querySelector('.chat-scroll .swarm-card')) return;
    const selection = selectionSeq;
    const seq = ++swarmHydrateSeq;
    try {
      const data = await Tomo.api('/api/sessions/' + encodeURIComponent(sessionId) + '/swarm');
      if (selection !== selectionSeq || seq !== swarmHydrateSeq || chatWrap.dataset.sessionId !== sessionId) return;
      TomoSwarm.hydrate(chatWrap.querySelector('.chat-scroll'), data);
    } catch (_) {}
  }

  function detachChat() {
    if (activeId) {
      var input = chatWrap.querySelector('.chat-input');
      if (input) composerDrafts.set(activeId, input.value);
    }
    if (chatHandle && chatHandle.destroy) chatHandle.destroy();
    chatHandle = null;
    stopHistoryPoll();
  }

  function currentUserId() {
    return loginUserId || 'web';
  }

  function stopHistoryPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (monitorEs) { try { monitorEs.close(); } catch (e) {} monitorEs = null; }
  }

  function refetchHistory(sessionId, cb) {
    // Don't wipe the live stream or inspector while the user is in an active turn.
    if (chatWrap.dataset.liveStream === '1' || historyLoading) {
      if (cb) cb([]);
      return;
    }
    var selection = selectionSeq;
    var request = ++historyRequest;
    Tomo.api(historyUrl(sessionId, true)).then(function (hist) {
      if (request !== historyRequest || selection !== selectionSeq || chatWrap.dataset.sessionId !== sessionId || chatWrap.dataset.liveStream === '1') return;
      var entries = hist.entries || [];
      rememberHistory(hist);
      if (entries.length === lastHistLen && !inspectorOpenKey) {
        if (cb) cb(entries);
        return;
      }
      lastHistLen = entries.length;
      renderHistory(entries, { preserveScroll: true });
      refreshQueryRail(sessionId);
      hydrateSwarmRuns(sessionId);
      if (!chatHandle) chatHandle = TomoChat.init(chatWrap);
      // History wipe removes HITL cards + todo dock — rehydrate from server.
      if (chatHandle && chatHandle.rehydratePending) {
        chatHandle.rehydratePending();
      }
      if (cb) cb(entries);
    }).catch(function () {});
  }

  function historyUrl(sessionId, refresh) {
    var url = '/api/sessions/' + encodeURIComponent(sessionId) + '/chat?limit=20';
    if (refresh && historyEntries.length && historyEntries[0].message_id) {
      url += '&since=' + encodeURIComponent(historyEntries[0].message_id);
    }
    return url;
  }

  function rememberHistory(hist) {
    historyEntries = hist.entries || [];
    historyHasMore = !!hist.has_more;
    historyBefore = hist.before || (historyEntries[0] && historyEntries[0].message_id);
  }

  function paintHistoryLoader() {
    var scroll = chatWrap.querySelector('.chat-scroll');
    var loader = scroll.querySelector('.chat-load-older');
    if (!historyHasMore) { if (loader) loader.remove(); return; }
    if (!loader) {
      loader = document.createElement('div');
      loader.className = 'chat-load-older';
      loader.setAttribute('role', 'status');
      scroll.prepend(loader);
    }
    loader.textContent = historyLoading ? 'Loading earlier messages…' : '';
  }

  async function loadOlderHistory(targetMessageId) {
    if (!historyHasMore || !historyBefore || historyLoading || chatWrap.dataset.liveStream === '1') return;
    var sessionId = chatWrap.dataset.sessionId;
    var selection = selectionSeq;
    var request = ++historyRequest;
    historyLoading = true;
    paintHistoryLoader();
    try {
      var url = historyUrl(sessionId, false) + (targetMessageId
        ? '&since=' + encodeURIComponent(targetMessageId)
        : '&before=' + encodeURIComponent(historyBefore));
      var hist = await Tomo.api(url);
      if (request !== historyRequest || selection !== selectionSeq || chatWrap.dataset.liveStream === '1') return;
      rememberHistory({ entries: targetMessageId ? (hist.entries || []) : (hist.entries || []).concat(historyEntries), has_more: hist.has_more, before: hist.before });
      lastHistLen = historyEntries.length;
      renderHistory(historyEntries, { preserveScroll: true, loadingOlder: true });
      hydrateSwarmRuns(sessionId);
      if (chatHandle && chatHandle.rehydratePending) chatHandle.rehydratePending();
    } catch (_) {
      if (selection === selectionSeq) Tomo.toast('Could not load earlier messages — try again', 'err');
    } finally {
      if (selection === selectionSeq) { historyLoading = false; paintHistoryLoader(); }
    }
  }

  chatWrap.querySelector('.chat-scroll').addEventListener('scroll', function () {
    if (this.scrollTop < 120 && !chatWrap.hasAttribute('aria-busy')) loadOlderHistory();
  }, { passive: true });

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
          statusEl.className = 'composer-status chat-status';
          statusEl.innerHTML = '<span class="composer-status-dot" aria-hidden="true"></span>Online';
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
            statusEl.className = 'composer-status chat-status';
            statusEl.innerHTML = '<span class="composer-status-dot" aria-hidden="true"></span>Online';
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
    const ids = s.agent_ids || (s.agent_id ? [s.agent_id] : []);
    var name = agentName(ids[0] || s.agent_id) || 'Chat';
    return s.channel === 'telegram' ? 'Telegram · ' + s.telegram_chat_id + ' · ' + name : name;
  }

  function workplaceLabel(wid) {
    if (!wid) return 'Personal space / legacy chat';
    var w = workplaces.find(function (x) { return x.id === wid; });
    return w ? Tomo.access.label(w) : 'Unavailable resource';
  }

  function fillWorkplaceSelect(selectEl, selectedId) {
    if (selectEl) Tomo.access.fill(selectEl, workplaces, selectedId || '');
  }

  function applyChatHeader(s) {
    var label = sessionLabel(s);
    // Operator-trust origin stays visible: unlinked Telegram chats run as an
    // explicitly allowlisted operator identity, never as a Member account.
    if (s && s.channel === 'telegram') {
      label += (s.user_id && s.user_id.indexOf('tg_') === 0)
        ? ' · Unlinked operator chat (allowlist only)'
        : ' · Linked account chat';
    }
    const title = (s.title || '').trim() || sessionLabel(s);
    document.getElementById('chatAgentName').textContent = title;
    var wid = (s && s.workplace_id) || chatWrap.dataset.workplaceId || '';
    document.getElementById('chatSessionMeta').textContent =
      label;
    chatWrap.dataset.agentName = label;
    chatWrap.dataset.workplaceId = wid;
    Tomo.access.updateBar(s, workplaces);
    // Compatibility metadata; visible access controls are above the chat.
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
    const focusedId = listEl.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    const rows = sessions.slice();
    const running = rows.filter(function (s) { return s.active_turn; });

    if (!rows.length) {
      listEl.innerHTML = '<div class="empty">No sessions yet</div>';
      return;
    }

    // Group by workplace (local folder context for the thread).
    var groups = {};
    var order = [];
    rows.forEach(function (s) {
      if (s.active_turn) return;
      var key = s.channel === 'telegram' ? '__telegram__' : ((s.workplace_id || '').trim() || '__none__');
      if (!groups[key]) {
        groups[key] = [];
        order.push(key);
      }
      groups[key].push(s);
    });
    // Local workplaces first, then tunnels, then none.
    order.sort(function (a, b) {
      if (a === b) return 0;
      if (a === '__telegram__') return -1;
      if (b === '__telegram__') return 1;
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

    // "tomo · /home/me/Project/tomo" → "tomo"; bare paths → last folder.
    function shortGroupLabel(key, head) {
      if (key === '__telegram__') return 'Telegram';
      if (key === '__none__') return 'Tomo workspace';
      var name = String(head || '').split(' \u00b7 ')[0].trim();
      var parts = name.split(/[\\/]+/).filter(Boolean);
      return parts.length ? parts[parts.length - 1] : (name || 'Workplace');
    }

    function sessionButton(s) {
      const label = sessionLabel(s);
      const sel = s.id === activeId && !searchMode ? ' selected' : '';
      var state = s.active_turn
        ? '<span class="session-running"><span class="session-running-dot" aria-hidden="true"></span>Running</span>'
        : '<span class="faint mono ts">' + esc(Tomo.ts ? Tomo.ts(s.updated_at) : '') + '</span>';
      return '<button type="button" class="session-item' + sel + '" data-id="' + esc(s.id) + '"' +
        (sel ? ' aria-current="true"' : '') +
        ' title="' + esc((s.title || 'Conversation') + (s.active_turn ? ' · Running in background — click to view' : '')) + '">' +
        '<div class="meta"><div class="title">' + esc(s.title || 'Conversation') + '</div>' +
        '<div class="desc">' + esc(label) + '</div></div>' +
        state + '</button>';
    }

    var html = running.length
      ? '<div class="session-group session-group-running" aria-label="Running chats">' +
        '<div class="session-group-head">Running <span class="session-running-count">' + running.length + '</span></div>' +
        running.map(sessionButton).join('') + '</div>'
      : '';
    order.forEach(function (key) {
      var list = groups[key] || [];
      // Newest first within group.
      list.sort(function (a, b) {
        return (b.updated_at || 0) - (a.updated_at || 0);
      });
      var head = key === '__telegram__' ? 'Telegram' : (key === '__none__' ? 'Personal space / legacy chat' : workplaceLabel(key));
      html += '<div class="session-group">' +
        '<div class="session-group-head" title="' + esc(head) + '">' + esc(shortGroupLabel(key, head)) +
        ' <span class="faint">' + list.length + '</span></div>' +
        list.map(sessionButton).join('') +
        '</div>';
    });
    listEl.innerHTML = html;

    listEl.querySelectorAll('.session-item').forEach(function (btn) {
      btn.addEventListener('click', function () { selectSession(btn.dataset.id); });
      if (btn.dataset.id === focusedId) btn.focus({ preventScroll: true });
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
    ++selectionSeq;
    cancelSwarmHydrate();
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
    if (window.Tomo && Tomo.stickScrollBottom) {
      Tomo.stickScrollBottom(scroll, { holdMs: 15000, times: [50, 200, 500, 1000, 2000, 4000, 8000] });
    } else if (scroll) {
      if (Tomo.scrollToBottomInstant) Tomo.scrollToBottomInstant(scroll);
      else scroll.scrollTop = scroll.scrollHeight;
    }
  }

  var queryTracking = { scroll: null, handler: null, raf: 0, observer: null };
  var queryPreview = null;
  var queryPreviewItem = null;
  var queryContextRaf = 0;
  var dirtyQueryTurns = new Set();
  var queryTargetTimer = null;

  function queryId(index) {
    return 'chat-query-' + String(index);
  }

  function firstLine(text) {
    return String(text == null ? '' : text)
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .find(function (line) { return !!line; }) || '';
  }

  function previewText(text, maxChars) {
    var value = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    if (!value) return '';
    var limit = Math.max(1, Number(maxChars) || 120);
    return value.length > limit ? value.slice(0, limit - 1) + '…' : value;
  }

  function buildQueryRecords(entries) {
    var records = [];
    var current = null;
    (entries || []).forEach(function (entry) {
      if (!entry) return;
      if (entry.type === 'user') {
        current = {
          index: records.length,
          id: entry.message_id ? 'chat-message-' + entry.message_id : queryId(records.length),
          prompt: String(entry.content || '').trim(),
          context: '',
        };
        records.push(current);
        return;
      }
      if (!current) return;
      if (entry.type === 'final') {
        var context = String(entry.content || '').trim();
        if (context) current.context = context;
      }
    });
    return records;
  }

  function queryRail() {
    return document.getElementById('chatQueryRail');
  }

  function queryTurn(queryIdValue) {
    var scroll = chatWrap.querySelector('.chat-scroll');
    if (!scroll || !queryIdValue) return null;
    return Array.from(scroll.querySelectorAll('.turn[data-query-id]')).find(function (turnEl) {
      return turnEl.dataset.queryId === queryIdValue;
    }) || null;
  }

  function setActiveQuery(queryIdValue) {
    var rail = queryRail();
    if (!rail) return;
    rail.querySelectorAll('.chat-query-item.is-active').forEach(function (item) {
      item.classList.remove('is-active');
      item.removeAttribute('aria-current');
    });
    if (!queryIdValue) return;
    var active = Array.from(rail.querySelectorAll('.chat-query-item')).find(function (item) {
      return item.dataset.queryId === queryIdValue;
    });
    if (active) {
      active.classList.add('is-active');
      active.setAttribute('aria-current', 'location');
      var railTop = rail.scrollTop;
      var railBottom = railTop + rail.clientHeight;
      var railRect = rail.getBoundingClientRect();
      var activeRect = active.getBoundingClientRect();
      var itemTop = activeRect.top - railRect.top + railTop;
      var itemBottom = itemTop + activeRect.height;
      var edgePadding = 18;
      if (itemTop < railTop + edgePadding || itemBottom > railBottom - edgePadding) {
        rail.scrollTop = Math.max(
          0,
          itemTop - Math.max(0, (rail.clientHeight - active.offsetHeight) / 2)
        );
      }
    }
    positionQueryPreview();
  }

  function syncQueryRailLayout() {
    var rail = queryRail();
    if (!rail) return;
    if (rail.hidden) {
      rail.style.justifyContent = '';
      return;
    }
    Array.from(rail.children).forEach(function (item, index) {
      item.dataset.number = String(index + 1);
      item.setAttribute('aria-label', 'Jump to message ' + item.dataset.number + ': ' + (item.dataset.prompt || 'User message'));
    });
    if (queryPreviewItem) showQueryPreview(queryPreviewItem);

    // Centering is useful for a short rail, but it can place the first items
    // above the scrollport once the list becomes taller than the rail.
    // Measure from a top-aligned state so the overflow case is deterministic.
    rail.style.justifyContent = 'flex-start';
    var isOverflowing = rail.scrollHeight > rail.clientHeight + 1;
    rail.style.justifyContent = isOverflowing ? 'flex-start' : 'center';
    positionQueryPreview();
  }

  function scheduleActiveQuery() {
    if (queryTracking.raf) return;
    var raf = window.requestAnimationFrame || function (cb) { return window.setTimeout(cb, 0); };
    queryTracking.raf = raf(function () {
      queryTracking.raf = 0;
      syncActiveQuery();
    });
  }

  function syncActiveQuery() {
    var scroll = chatWrap.querySelector('.chat-scroll');
    if (!scroll) return;
    var turns = Array.from(scroll.querySelectorAll('.turn[data-query-id]'));
    if (!turns.length) {
      setActiveQuery('');
      return;
    }
    var bounds = scroll.getBoundingClientRect();
    var center = bounds.top + (scroll.clientHeight / 2);
    var nearest = turns.reduce(function (best, turnEl) {
      var rect = turnEl.getBoundingClientRect();
      var distance = Math.max(rect.top - center, center - rect.bottom, 0);
      return !best || distance < best.distance ? { turn: turnEl, distance: distance } : best;
    }, null);
    setActiveQuery(nearest && nearest.turn.dataset.queryId);
  }

  function hideQueryPreview() {
    if (queryPreview) queryPreview.hidden = true;
    if (queryPreviewItem) queryPreviewItem.removeAttribute('aria-describedby');
    queryPreviewItem = null;
  }

  function positionQueryPreview() {
    if (!queryPreviewItem || !queryPreview || queryPreview.hidden) return;
    var rail = queryRail();
    var marker = queryPreviewItem.getBoundingClientRect();
    var bounds = rail.getBoundingClientRect();
    if (marker.bottom <= bounds.top || marker.top >= bounds.bottom) {
      hideQueryPreview();
      return;
    }
    var main = rail.parentElement.getBoundingClientRect();
    var height = queryPreview.offsetHeight;
    queryPreview.style.top = Math.max(12, Math.min(
      marker.top - main.top + marker.height / 2 - height / 2,
      main.height - height - 12
    )) + 'px';
  }

  function showQueryPreview(item) {
    if (queryPreviewItem && queryPreviewItem !== item) hideQueryPreview();
    if (!queryPreview) {
      queryPreview = document.createElement('div');
      queryPreview.id = 'chatQueryPreview';
      queryPreview.className = 'chat-query-card';
      queryPreview.setAttribute('role', 'tooltip');
      queryPreview.innerHTML = '<div class="chat-query-meta"></div><div class="chat-query-title"></div>' +
        '<div class="chat-query-context"></div><div class="chat-query-hint">Click to jump · ↑ ↓ to browse</div>';
      queryRail().parentElement.appendChild(queryPreview);
    }
    queryPreviewItem = item;
    item.setAttribute('aria-describedby', queryPreview.id);
    queryPreview.hidden = false;
    queryPreview.dataset.state = item.dataset.state;
    queryPreview.dataset.keyboard = String(document.activeElement === item);
    var labels = { answered: 'Response', responding: 'Responding', working: 'Working', queued: 'Queued', steering: 'Steering', empty: 'No text response' };
    queryPreview.querySelector('.chat-query-meta').textContent = 'Your message · ' + (labels[item.dataset.state] || labels.empty);
    queryPreview.querySelector('.chat-query-title').textContent = item.dataset.prompt || 'User message';
    var context = queryPreview.querySelector('.chat-query-context');
    var placeholders = {
      working: 'The agent is working on this message…',
      queued: 'This message is queued for the next turn.',
      steering: 'Sending this direction to the active turn…',
      empty: 'No text response for this message.',
    };
    context.textContent = item.dataset.context || placeholders[item.dataset.state] || placeholders.empty;
    context.classList.toggle('is-empty', !item.dataset.context);
    positionQueryPreview();
  }

  function syncQueryContext(turn) {
    if (!turn.isConnected) return;
    var rail = queryRail();
    if (!rail) return;
    var item = Array.from(rail.children).find(function (candidate) {
      return candidate.dataset.queryId === turn.dataset.queryId;
    });
    if (!item) return;
    // Only primary-agent bubbles count; nested inspectors and tool output don't.
    var bodies = Array.from(turn.querySelectorAll(':scope > .msg.assistant .bubble-body'));
    var body = bodies.reverse().find(function (candidate) { return candidate.textContent.trim(); });
    var context = body ? previewText(body.textContent, 240) : '';
    var turns = Array.from(queryTracking.scroll.querySelectorAll('.turn[data-query-id]')).filter(function (candidate) {
      return !candidate.querySelector(':scope > .msg-queued, :scope > .msg-steering');
    });
    var working = chatWrap.dataset.liveStream === '1' && turn === turns[turns.length - 1];
    var pending = turn.querySelector(':scope > .msg-queued, :scope > .msg-steering');
    var emptyState = pending ? (pending.classList.contains('msg-steering') ? 'steering' : 'queued') : (working ? 'working' : 'empty');
    var state = context ? (body.closest('.msg').classList.contains('streaming') ? 'responding' : 'answered') : emptyState;
    if (item.dataset.context === context && item.dataset.state === state) return;
    item.dataset.context = context;
    item.dataset.state = state;
    if (queryPreviewItem === item) showQueryPreview(item);
  }

  function scheduleQueryContexts(turn) {
    if (turn) dirtyQueryTurns.add(turn);
    else if (queryTracking.scroll) {
      queryTracking.scroll.querySelectorAll('.turn[data-query-id]').forEach(function (el) { dirtyQueryTurns.add(el); });
    }
    if (queryContextRaf) return;
    queryContextRaf = window.requestAnimationFrame(function () {
      queryContextRaf = 0;
      dirtyQueryTurns.forEach(syncQueryContext);
      dirtyQueryTurns.clear();
    });
  }

  function bindQueryTracking(scroll) {
    if (!scroll) return;
    if (queryTracking.scroll && queryTracking.handler) {
      queryTracking.scroll.removeEventListener('scroll', queryTracking.handler);
    }
    if (queryTracking.observer) queryTracking.observer.disconnect();
    dirtyQueryTurns.clear();
    queryTracking.scroll = scroll;
    queryTracking.handler = scheduleActiveQuery;
    scroll.addEventListener('scroll', queryTracking.handler, { passive: true });
    queryTracking.observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        var el = mutation.target.nodeType === 1 ? mutation.target : mutation.target.parentElement;
        var turn = el && el.closest('.turn[data-query-id]');
        if (turn) scheduleQueryContexts(turn);
        else mutation.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.matches('.turn[data-query-id]')) scheduleQueryContexts(node);
          node.querySelectorAll('.turn[data-query-id]').forEach(function (el) { scheduleQueryContexts(el); });
        });
      });
    });
    queryTracking.observer.observe(scroll, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    scheduleQueryContexts();
    scheduleActiveQuery();
  }

  function createQueryRailItem(record) {
    var item = document.createElement('button');
    item.type = 'button';
    item.className = 'chat-query-item';
    item.dataset.queryId = record.id;
    item.dataset.number = String(record.index + 1);
    item.dataset.prompt = previewText(record.prompt, 260);
    item.dataset.context = previewText(record.context, 240);
    item.dataset.state = record.context ? 'answered' : 'empty';
    item.setAttribute('aria-label', 'Jump to message ' + item.dataset.number + ': ' + (item.dataset.prompt || 'User message'));
    var marker = document.createElement('span');
    marker.className = 'chat-query-marker';
    marker.setAttribute('aria-hidden', 'true');
    item.appendChild(marker);
    item.addEventListener('mouseenter', function () { showQueryPreview(item); });
    item.addEventListener('mouseleave', function () { if (document.activeElement !== item) hideQueryPreview(); });
    item.addEventListener('blur', hideQueryPreview);
    item.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { hideQueryPreview(); item.blur(); return; }
      var items = Array.from(queryRail().children);
      var index = items.indexOf(item);
      var next = { ArrowUp: index - 1, ArrowDown: index + 1, Home: 0, End: items.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      items[Math.max(0, Math.min(items.length - 1, next))].focus();
    });
    item.addEventListener('click', async function () {
      var turn = queryTurn(item.dataset.queryId);
      var selection = selectionSeq;
      if (!turn && record.messageId) {
        await loadOlderHistory(record.messageId);
        if (selection !== selectionSeq) return;
        turn = queryTurn(record.id);
      }
      if (!turn) return;
      var scroll = chatWrap.querySelector('.chat-scroll');
      if (scroll._tomoStickCleanup) scroll._tomoStickCleanup();
      var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      turn.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
      setActiveQuery(item.dataset.queryId);
      turn.classList.remove('is-query-target');
      void turn.offsetWidth;
      turn.classList.add('is-query-target');
      if (queryTargetTimer) window.clearTimeout(queryTargetTimer);
      queryTargetTimer = window.setTimeout(function () {
        turn.classList.remove('is-query-target');
        queryTargetTimer = null;
      }, reduced ? 0 : 900);
    });
    item.addEventListener('focus', function () { setActiveQuery(item.dataset.queryId); showQueryPreview(item); });
    return item;
  }

  function renderQueryRail(records) {
    var rail = queryRail();
    if (!rail) return;
    hideQueryPreview();
    rail.innerHTML = '';
    rail.hidden = !records.length;
    records.forEach(function (record) { rail.appendChild(createQueryRailItem(record)); });
    syncQueryRailLayout();
  }

  function appendLiveQuery(detail) {
    var rail = queryRail();
    if (!rail || !detail || !detail.queryId) return;
    if (rail.querySelector('.chat-query-item[data-query-id="' + detail.queryId + '"]')) return;
    rail.hidden = false;
    rail.appendChild(createQueryRailItem({
      id: detail.queryId,
      index: detail.queryIndex,
      prompt: String(detail.text || ''),
      context: '',
    }));
    syncQueryRailLayout();
    var scroll = chatWrap.querySelector('.chat-scroll');
    if (scroll && queryTracking.scroll !== scroll) bindQueryTracking(scroll);
    scheduleQueryContexts(queryTurn(detail.queryId));
    scheduleActiveQuery();
  }

  function removeLiveQuery(queryIdValue) {
    var rail = queryRail();
    if (!rail || !queryIdValue) return;
    var item = Array.from(rail.querySelectorAll('.chat-query-item')).find(function (candidate) {
      return candidate.dataset.queryId === queryIdValue;
    });
    if (item === queryPreviewItem) hideQueryPreview();
    if (item) item.remove();
    if (!rail.querySelector('.chat-query-item')) rail.hidden = true;
    syncQueryRailLayout();
    scheduleActiveQuery();
  }

  function renderHistory(entries, opts) {
    const scroll = chatWrap.querySelector('.chat-scroll');
    var preserve = opts && opts.preserveScroll;
    var atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 80;
    var previousTop = scroll.scrollTop;
    var anchor = preserve && Array.from(scroll.querySelectorAll('.turn[data-query-id]')).find(function (el) {
      return el.getBoundingClientRect().bottom > scroll.getBoundingClientRect().top;
    });
    var anchorId = anchor && anchor.dataset.queryId;
    var anchorTop = anchor && anchor.getBoundingClientRect().top;
    if (preserve && scroll._tomoStickCleanup) scroll._tomoStickCleanup();
    var queryRecords = buildQueryRecords(entries);
    renderQueryRail(fullQueryRecords(queryRecords));
    scroll.innerHTML = '';
    if (window.Tomo && Tomo.clearTodoDock) Tomo.clearTodoDock(chatWrap);
    if (!entries.length) {
      bindQueryTracking(scroll);
      scroll.innerHTML = '<div class="chat-empty"><div class="big">Start a conversation</div><div>Ask for a team in chat or turn on Team for one message.</div></div>';
      stickChatScrollBottom(scroll);
      return;
    }
    // First paint of plan from history so the dock is not blank while pending
    // rehydrate (or when the in-memory store was lost after process restart).
    if (window.Tomo && Tomo.restoreTodosFromHistory) {
      Tomo.restoreTodosFromHistory(chatWrap, entries);
    }

    // ── Per-turn state ──────────────────────────────────────────────
    var turn = null;
    var turnId = 0;
    var delegateCounter = 0;          // unique per delegate call within a turn (legacy)
    var queryCursor = 0;
    var agentToKey = {};               // agent_id → current buffer key (legacy fallback)
    var subagentSet = new Set();
    var subagentBuffers = new Map();  // key → buffer
    var turnBuffers = new Map();      // current turn: key → buffer
    var swarmCard = null;
    var detailPanel = null;

    function getBuffer(key) {
      if (!turnBuffers.has(key)) {
        var buf = { events: [], name: '', task: '', status: 'running', row: null, aid: '', turnId: turnId, key: key };
        turnBuffers.set(key, buf);
        subagentBuffers.set(key, buf);
      }
      return turnBuffers.get(key);
    }

    function entryDcid(e) {
      if (!e) return '';
      if (e.delegate_call_id) return String(e.delegate_call_id);
      var p = e.params || {};
      if (p.delegate_call_id) return String(p.delegate_call_id);
      return '';
    }

    function keyForEntry(e, aid) {
      var dcid = entryDcid(e);
      if (dcid) return 'd:' + dcid;
      var id = aid || (e && e.agent_id) || '';
      return agentToKey[id] || id;
    }

    function keyForAid(aid) {
      return agentToKey[aid] || aid;
    }

    function startTurn(record, steered) {
      turnId++;
      turn = document.createElement('div');
      turn.className = 'turn';
      if (record) {
        turn.dataset.queryId = record.id;
        turn.dataset.queryIndex = String(record.index);
      }
      if (steered) turn.dataset.steered = '1';
      scroll.appendChild(turn);
      swarmCard = null;
      // A steer splits the visible conversation, not the running delegation.
      if (!steered) {
        subagentSet = new Set();
        turnBuffers = new Map();
        agentToKey = {};
        delegateCounter = 0;
      }
      var oldPanel = chatWrap.querySelector('.subagent-inspector, .detail-panel');
      if (oldPanel) oldPanel.remove();
      detailPanel = null;
    }

    // Render a primary-agent final answer bubble in place. Intermediate
    // reasoning is rendered as a collapsible timeline card below.
    function appendAssistantMessage(aid, text) {
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
      if (window.TomoGenerativeUI) TomoGenerativeUI.placeBlocks(turn);
    }

    function appendReasoningCard(text) {
      if (!text || !turn) return;
      if (window.Tomo && Tomo.buildReasoningCard) {
        turn.appendChild(Tomo.buildReasoningCard(text));
        return;
      }
      var details = document.createElement('details');
      details.className = 'reasoning-card';
      details.innerHTML = '<summary>Reasoning</summary><pre></pre>';
      details.querySelector('pre').textContent = text;
      turn.appendChild(details);
    }

    function appendGenerativeUI(spec) {
      if (!spec || !spec.ui_id || (!spec.tree && !spec.patch) || !window.TomoGenerativeUI) return;
      turn.querySelectorAll('.tool[data-tool-name="render_ui"]').forEach(function (card) {
        card.classList.remove('expanded');
        card.classList.add('is-ui-ledger');
        if (card._chip) card._chip.textContent = 'rendered';
      });
      var mounted = TomoGenerativeUI.mount(turn, spec, {
        dispatch: function (action) {
          if (chatHandle && chatHandle.uiAction) return chatHandle.uiAction(action);
          var body = '[UI action]\n' + JSON.stringify(action);
          if (chatHandle && chatHandle.send) return chatHandle.send(body);
          return null;
        },
        sessionId: chatWrap.dataset.sessionId || '',
        asBlock: true,
      });
      if (mounted) {
        var block = mounted.closest
          ? (mounted.closest('.gen-ui-block') || mounted)
          : mounted;
        block.classList.add('is-hero');
        TomoGenerativeUI.placeBlocks(turn);
      }
    }

    function ensureSwarmCard() {
      if (swarmCard) return swarmCard;
      swarmCard = document.createElement('div');
      swarmCard.className = 'swarm-card';
      turn.appendChild(swarmCard);
      if (window.TomoSwarm) TomoSwarm.lanes(swarmCard);
      return swarmCard;
    }

    function addSwarmRow(key, aid, name, task, idx, total) {
      var card = ensureSwarmCard();
      var row = Tomo.buildSwarmRow({
        key: key, aid: aid, name: name, task: task, idx: idx || 1, total: total || 1,
        historic: true,
      });
      row._openTrace = function () { openDetailPanel(row); };
      row._wired = true;
      row.addEventListener('click', function () { row._openTrace(); });
      (window.TomoSwarm ? TomoSwarm.lanes(card) : card).appendChild(row);
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
      Tomo.swarmRowDone(buf.row, buf.status);
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
      Tomo.swarmRowEvent(buf.row, kind, data);
    }

    function makeToolCollapsible(card) {
      if (window.Tomo && Tomo.wireToolCard) Tomo.wireToolCard(card);
    }

    function toolCallStillRunning(entries, index) {
      // Unpaired tool_call while the turn has not finished — still executing
      // after a mid-turn refresh. Prefer call_id pairing so parallel tools
      // don't all clear when the first result arrives.
      var e = entries[index];
      if (!e || e.type !== 'tool_call') return false;
      var last = entries[entries.length - 1];
      if (!last || last.type === 'final' || last.type === 'error') return false;
      var aid = e.agent_id || '';
      var callId = (e.call_id || '').toString();
      for (var i = index + 1; i < entries.length; i++) {
        var n = entries[i];
        if (n.type === 'tool_output' && (n.agent_id || '') === aid) {
          if (callId) {
            if ((n.call_id || '').toString() === callId) return false;
            continue;
          }
          return false;
        }
        if (n.type === 'user' || n.type === 'final' || n.type === 'error') return false;
      }
      return true;
    }

    function buildHistoryToolCard(fn, params, running, callId) {
      if (window.Tomo && Tomo.buildToolCard) {
        return Tomo.buildToolCard({
          tool: fn || 'tool',
          args: params || {},
          running: !!running,
          call_id: callId || '',
        });
      }
      var card = document.createElement('div');
      card.className = 'tool' + (running ? ' loading' : ' ok');
      if (callId) card.dataset.callId = callId;
      card.dataset.toolName = fn || 'tool';
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
        r.classList.toggle('selected', r === (buf.row || rowOrAid));
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
      if (e.type === 'background_job') {
        startTurn();
        var jobMarker = document.createElement('p');
        jobMarker.className = 'process-note background-job-marker';
        jobMarker.dataset.backgroundJobIds = (e.background_job_ids || []).slice().sort().join(',');
        jobMarker.textContent = e.content || 'Background jobs completed';
        if (e.message_id) { jobMarker.id = 'message-' + e.message_id; jobMarker.dataset.messageId = String(e.message_id); }
        turn.appendChild(jobMarker);
        return;
      }
      if (e.type === 'user') {
        var queryRecord = queryRecords[queryCursor] || {
          index: queryCursor,
          id: queryId(queryCursor),
          prompt: e.content || '',
          context: '',
        };
        queryCursor++;
        startTurn(queryRecord, !!(e.steered || (e.params && e.params.steered)));
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
        var dcid = entryDcid(e);
        var key;
        if (dcid) {
          key = 'd:' + dcid;
        } else {
          delegateCounter++;
          key = turnId + ':' + delegateCounter;
        }
        agentToKey[aid] = key;
        var buf = getBuffer(key);
        buf.name = name; buf.aid = aid; buf.task = task;
        addSwarmRow(key, aid, name, task, idx, total);
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
        var key = keyForEntry(e, aid);
        if (!key || !turnBuffers.has(key)) {
          var dcid = entryDcid(e);
          if (dcid) {
            key = 'd:' + dcid;
          } else {
            delegateCounter++;
            key = turnId + ':' + delegateCounter;
          }
          agentToKey[aid] = key;
        }
        var buf = getBuffer(key);
        buf.name = name; buf.aid = aid; buf.task = task || buf.task;
        if (!buf.row) addSwarmRow(key, aid, name, task, idx, total);
        if (buf.row) buf.row.classList.add('active');
        return;
      }

      if (e.type === 'subagent_done') {
        var p = e.params || {};
        markSwarmDone(keyForEntry(e, e.agent_id || ''), p.status || 'ok');
        return;
      }

      if (e.type === 'ui') {
        var uiAid = e.agent_id || '';
        if (subagentSet.has(uiAid)) {
          bufferEvent(keyForEntry(e, uiAid), 'ui', e.params || {});
          return;
        }
        var spec = e.params || null;
        if (!spec || !spec.tree) {
          try { spec = JSON.parse(e.content || '{}'); } catch (_) { spec = null; }
        }
        if (spec) {
          spec.agent_id = e.agent_id || '';
          appendGenerativeUI(spec);
        }
        return;
      }

      if (e.type === 'tool_call') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForEntry(e, aid);
          bufferEvent(key, 'tool', {
            tool: e.function,
            args: e.params,
            call_id: e.call_id || '',
          });
          bumpSwarmProgress(key);
        } else {
          var historyTool = buildHistoryToolCard(
            e.function,
            e.params,
            toolCallStillRunning(entries, entryIdx),
            e.call_id || ''
          );
          if (e.message_id) {
            historyTool.dataset.messageId = String(e.message_id);
            historyTool.id = 'message-' + e.message_id;
          }
          turn.appendChild(historyTool);
        }
        return;
      }

      if (e.type === 'tool_output') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForEntry(e, aid);
          bufferEvent(key, 'tool_result', {
            result: e.content,
            error: e.error,
            tool: e.function || '',
            call_id: e.call_id || '',
          });
          bumpSwarmProgress(key);
        } else {
          var last = window.Tomo && Tomo.findToolCard
            ? Tomo.findToolCard(turn, {
                call_id: e.call_id || '',
                tool: e.function || '',
              })
            : null;
          if (!last) {
            var loading = turn.querySelectorAll('.tool.loading');
            last = loading[0] || null;
          }
          var resultText = e.content || '';
          if (last) {
            if (window.Tomo && Tomo.finishToolCard) {
              Tomo.finishToolCard(last, resultText, !!e.error);
            } else if (last._res) {
              last._res.textContent = resultText;
              last.classList.remove('loading');
              last.classList.remove('running');
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
          // Keep todo dock current as later tool_outputs walk history.
          if (!e.error && (e.function || '') === 'todo' && window.Tomo && Tomo.parseTodosResult) {
            var histTodos = Tomo.parseTodosResult(resultText);
            if (histTodos && histTodos.length) Tomo.upsertTodoPanel(chatWrap, histTodos);
          }
          // Fallback: mount from render_ui tool_output when a dedicated `ui`
          // history row is missing (or was skipped).
          if (!e.error && resultText && resultText.charAt(0) === '{') {
            try {
              var uiSpec = JSON.parse(resultText);
              if (uiSpec && uiSpec.ui_id && (uiSpec.tree || uiSpec.patch)) {
                appendGenerativeUI(uiSpec);
              }
            } catch (_) {}
          }
        }
        return;
      }

      if (e.type === 'thinking') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          var key = keyForEntry(e, aid);
          bufferEvent(key, 'thinking', { content: e.content });
          bumpSwarmProgress(key);
          return;
        }
        // Primary agent: render intermediate text as a collapsible card in
        // place so it stays interleaved with the tool cards that follow.
        var text = (e.content || '').trim();
        if (!text || text.indexOf('[Swarm]') === 0) return;
        appendReasoningCard(text);
        return;
      }

      if (e.type === 'final' || e.type === 'subagent_final') {
        var aid = e.agent_id || '';
        var text = (e.content || '').trim();
        if (!text || text.indexOf('[Swarm]') === 0) return;
        // subagent_final never closes the parent turn; always buffer under swarm.
        if (e.type === 'subagent_final' || subagentSet.has(aid)) {
          var key = keyForEntry(e, aid);
          if (aid) subagentSet.add(aid);
          bufferEvent(key, 'final', { content: text });
          markSwarmDone(key, 'done');
        } else {
          appendAssistantMessage(aid, text);
        }
        return;
      }

      if (e.type === 'error') {
        var aid = e.agent_id || '';
        if (subagentSet.has(aid)) {
          markSwarmDone(keyForEntry(e, aid), 'error');
        } else {
          var row = document.createElement('div');
          row.className = 'msg error';
          row.innerHTML = '<div class="bubble"><div class="bubble-body" style="color:var(--danger)">' + esc(e.content || 'Error') + '</div></div>';
          turn.appendChild(row);
        }
        return;
      }

      if (e.type === 'compact') {
        // /compact marker — a quiet divider with the summary on expand.
        if (!turn) startTurn();
        var summary = (e.content || '').trim();
        var row = document.createElement('div');
        row.className = 'msg compact-note';
        row.innerHTML =
          '<details class="compact-divider"><summary>' +
          '<span class="compact-mark" aria-hidden="true">✂</span> ' +
          esc('Earlier messages compacted') +
          '</summary>' +
          (summary ? '<div class="compact-summary">' + esc(summary) + '</div>' : '') +
          '</details>';
        turn.appendChild(row);
        return;
      }
    });

    if (inspectorOpenKey) {
      var reopen = scroll.querySelector('.swarm-row[data-buffer-key="' + inspectorOpenKey + '"]');
      if (reopen) openDetailPanel(reopen);
    }

    bindQueryTracking(scroll);
    paintHistoryLoader();
    if (preserve && (!atBottom || opts.loadingOlder)) {
      var restored = anchorId && queryTurn(anchorId);
      var behavior = scroll.style.scrollBehavior;
      scroll.style.scrollBehavior = 'auto';
      scroll.scrollTop = restored ? previousTop + restored.getBoundingClientRect().top - anchorTop : previousTop;
      scroll.style.scrollBehavior = behavior;
    } else {
      stickChatScrollBottom(scroll);
    }
  }

  function closeMobileRail() {
    var close = document.getElementById('navToggle');
    if (close && window.matchMedia('(max-width: 760px)').matches &&
        document.documentElement.classList.contains('is-rail-open')) close.click();
  }

  async function selectSession(sessionId, opts) {
    closeMobileRail();
    const s = sessions.find(function (x) { return x.id === sessionId; });
    if (!s) return;
    if (activeId === sessionId && chatHandle && !searchMode && !(opts && opts.pendingMessage)) return;
    var selection = ++selectionSeq;
    ++historyRequest;
    historyEntries = [];
    historyHasMore = false;
    historyBefore = null;
    historyLoading = false;
    historyQueries = [];
    detachChat();
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
    refreshQueryRail(sessionId);
    cancelSwarmHydrate();
    // Keep the login account id — never adopt another session's user_id.
    chatWrap.dataset.userId = currentUserId();
    chatWrap.dataset.agentIds = ids.join(',');
    chatWrap.dataset.agentsJson = agentsJsonFor(ids);
    chatWrap.dataset.agentName = label;
    chatWrap.dataset.chatInit = '0';
    delete chatWrap.dataset.ctxInit;
    delete chatWrap.dataset.agentId;

    applyChatHeader(s);
    renderAvatars(ids);

    var input = chatWrap.querySelector('.chat-input');
    if (input) { input.value = composerDrafts.get(sessionId) || ''; input.disabled = true; }
    var sendBtn = chatWrap.querySelector('.chat-send');
    if (sendBtn) sendBtn.disabled = true;
    renderHistory([]);
    var loading = chatWrap.querySelector('.chat-empty');
    if (loading) loading.innerHTML = '<div class="big">Loading conversation…</div><div>Your other chats keep running.</div>';
    chatWrap.setAttribute('aria-busy', 'true');
    lastHistLen = -1;
    inspectorOpenKey = null;

    try {
      const hist = await Tomo.api(historyUrl(sessionId, false));
      if (selection !== selectionSeq) return;
      rememberHistory(hist);
      lastHistLen = historyEntries.length;
      renderHistory(hist.entries || []);
      hydrateSwarmRuns(sessionId);
      if (input) input.disabled = false;
      chatWrap.removeAttribute('aria-busy');
      chatHandle = TomoChat.init(chatWrap);
      // init may re-touch markdown; stick again after layout settles
      var scrollEl = chatWrap.querySelector('.chat-scroll');
      stickChatScrollBottom(scrollEl);
      // Web fonts / late paint can reflow long prose after the first pin.
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(function () {
          if (chatWrap.dataset.sessionId !== sessionId) return;
          if (scrollEl && typeof scrollEl._tomoStickGo === 'function') {
            scrollEl._tomoStickGo();
          }
        });
      }

      // Mid-turn / HITL wait: rehydrate cards + re-attach listen stream.
      // Prefer server active_turn (pending API) over history heuristics —
      // last entry can be a subagent final while the parent turn still runs.
      if (!pending && chatHandle) {
        var entries = hist.entries || [];
        var last = entries[entries.length - 1];
        var maybeMidTurn = s.active_turn === true ||
          (typeof s.active_turn !== 'boolean' && last && last.type !== 'final' && last.type !== 'error');
        var tryResume = function () {
          var statusEl = chatWrap.querySelector('.chat-status');
          if (statusEl) {
            statusEl.className = 'composer-status chat-status warn';
            statusEl.innerHTML = '<span class="composer-status-dot" aria-hidden="true"></span>Busy';
          }
          if (chatHandle.resume && chatHandle.resume()) return;
          startHistoryPoll(sessionId);
        };
        var afterProbe = function (needs) {
          if (selection !== selectionSeq) return;
          if (needs || maybeMidTurn) tryResume();
        };
        if (chatHandle.rehydratePending) {
          chatHandle.rehydratePending().then(afterProbe);
        } else if (maybeMidTurn) {
          tryResume();
        }
      }

      if (pending && chatHandle && chatHandle.send) chatHandle.send(pending);
    } catch (e) {
      if (selection === selectionSeq) {
        chatWrap.removeAttribute('aria-busy');
        Tomo.toast('Could not load session — select it again to retry', 'err');
      }
    }
  }

  async function refreshSessions() {
    try {
      // Read-only: pruning here races a different chat's first-message create.
      const data = await Tomo.api('/api/sessions');
      if (!data) return;
      sessions = data.sessions || [];
      agents = {};
      (data.agents || []).forEach(function (a) { agents[a.id] = a; });
      try {
        var wpData = await Tomo.api('/api/access/resources');
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
        ++selectionSeq;
        delete chatWrap.dataset.sessionId;
        cancelSwarmHydrate();
        stopHistoryPoll();
        if (chatHandle && chatHandle.destroy) chatHandle.destroy();
        chatHandle = null;
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
    closeMobileRail();
    detachChat();
    ++selectionSeq;
    ++historyRequest;
    historyEntries = [];
    historyHasMore = false;
    historyBefore = null;
    historyLoading = false;
    historyQueries = [];
    cancelSwarmHydrate();
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
      title: 'New conversation',
      agent_ids: ids,
      agent_id: ids[0],
      user_id: currentUserId(),
      message_count: 0,
      workplace_id: draftWorkplaceId,
    };
    applyChatHeader(draft);
    renderAvatars(ids);
    renderHistory([]);
    chatWrap.removeAttribute('aria-busy');
    var input = chatWrap.querySelector('.chat-input');
    if (input) { input.value = ''; input.disabled = false; }
    delete chatWrap.dataset.nextExecutionMode;
    chatHandle = TomoChat.init(chatWrap);
    if (input) input.focus();
    if (pending && chatHandle && chatHandle.send) chatHandle.send(pending);
  }

  function startNewChat(agentIds, opts) {
    var ids = agentIds && agentIds.length ? agentIds.slice() : allEnabledAgentIds().slice(0, 1);
    if (!ids.length) {
      Tomo.toast('No enabled agents', 'err');
      return;
    }
    // Persist only on first message (chat.js ensureSession).
    openDraft(ids, opts);
  }

  function startDefaultSwarm(opts) {
    startNewChat(allEnabledAgentIds().slice(0, 1), opts);
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
    var current = sessions.find(function (s) { return s.id === activeId; });
    if (current) { current.active_turn = true; renderList(); }
    scheduleQueryContexts();
    stopHistoryPoll();
  });
  chatWrap.addEventListener('tomo:user-turn', function (ev) {
    appendLiveQuery(ev.detail || {});
  });
  chatWrap.addEventListener('tomo:user-turn-removed', function (ev) {
    var detail = ev.detail || {};
    removeLiveQuery(detail.queryId || '');
  });
  window.addEventListener('resize', syncQueryRailLayout);
  if (queryRail()) queryRail().addEventListener('scroll', positionQueryPreview, { passive: true });
  chatWrap.addEventListener('tomo:turn-end', function () {
    var current = sessions.find(function (s) { return s.id === activeId; });
    if (current) { current.active_turn = false; renderList(); }
    scheduleQueryContexts();
    var sid = chatWrap.dataset.sessionId;
    if (!sid) return;
    // Live stream already painted this turn. Forcing renderHistory() here
    // clears .chat-scroll (scroll jumps to top) then rebuilds — that is the
    // "thrown upward" kick after the agent finishes responding.
    // Only sync the length marker so a later poll won't wipe either.
    var selection = selectionSeq;
    var request = ++historyRequest;
    Tomo.api(historyUrl(sid, true)).then(function (hist) {
      if (request !== historyRequest || selection !== selectionSeq || chatWrap.dataset.sessionId !== sid) return;
      rememberHistory(hist);
      lastHistLen = (hist.entries || []).length;
      paintHistoryLoader();
      refreshQueryRail(sid);
    }).catch(function () {});
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
    scheduleQueryContexts();
    refreshSessions().then(function () {
      const cur = sessions.find(function (x) { return x.id === activeId; });
      if (cur) applyChatHeader(cur);
    });
  });
  chatWrap.addEventListener('tomo:chat-cleared', function () {
    ++historyRequest;
    historyEntries = [];
    historyHasMore = false;
    historyBefore = null;
    historyLoading = false;
    historyQueries = [];
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
      var wpData = await Tomo.api('/api/access/resources');
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
      if (!el.disabled) el.checked = el.value === allEnabledAgentIds()[0];
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
      startNewChat(ids.length ? ids : allEnabledAgentIds().slice(0, 1), {
        workplaceId: draftWorkplaceId,
      });
    });
  }

  var createProjectBtn = document.getElementById('newChatCreateProject');
  if (createProjectBtn) createProjectBtn.addEventListener('click', async function () {
    try { var project = await Tomo.access.createProject(); if (project) await refreshWorkplacesAndSelect(project.id); }
    catch (e) { Tomo.toast(e.message || 'Project creation failed', 'err'); }
  });
  chatWrap.addEventListener('tomo:access-changed', function () { refreshSessions(); });

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

  // Discover inbound Telegram conversations and update an open history.
  var channelRefreshBusy = false;
  setInterval(async function () {
    if (document.hidden || channelRefreshBusy) return;
    channelRefreshBusy = true;
    try {
      var data = await Tomo.api('/api/sessions');
      if (!data) return;
      sessions = data.sessions || [];
      renderList();
      var current = sessions.find(function (s) { return s.id === activeId; });
      if (current) {
        if (current.channel === 'telegram') applyChatHeader(current);
        var selectedSession = current.id;
        var needsContinuation = current.active_turn;
        refetchHistory(selectedSession, function () {
          if (activeId === selectedSession && chatWrap.dataset.sessionId === selectedSession &&
              needsContinuation && chatHandle && chatHandle.resume) chatHandle.resume();
        });
      }
    } catch (_) {} finally { channelRefreshBusy = false; }
  }, 5000);

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

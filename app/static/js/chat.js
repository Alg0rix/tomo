/* chat.js — streaming chat client (agent detail + swarm sessions page).
 * Event protocol: state · turn.start · session · thinking · tool · tool_result · delta · done · delegate · error · heartbeat
 */
(function () {
  "use strict";

  function esc(s) { return Tomo.escapeHtml(s); }

  function renderMarkdown(el) {
    // Idempotent: already-parsed HTML must not be re-read via textContent
    // (that flattens markdown on history + re-init).
    if (!el || el.dataset.md === '1') return;
    var text = el.textContent;
    if (window.TomoMarkdown && TomoMarkdown.renderInto) {
      TomoMarkdown.renderInto(el, text);
      return;
    }
    // Fallback if markdown.js not loaded.
    if (typeof marked !== 'undefined') {
      try {
        el.innerHTML = marked.parse(text, { breaks: true, gfm: true });
      } catch (e) {
        el.innerHTML = esc(text).replace(/\n/g, '<br>');
      }
    } else {
      el.innerHTML = esc(text).replace(/\n/g, '<br>');
    }
    el.classList.add('chat-prose');
    el.dataset.md = '1';
  }

  function setMarkdown(el, text) {
    if (!el) return;
    el.dataset.md = '0';
    if (window.TomoMarkdown && TomoMarkdown.renderInto) {
      TomoMarkdown.renderInto(el, text == null ? '' : String(text));
      return;
    }
    el.textContent = text == null ? '' : String(text);
    renderMarkdown(el);
  }

  function agentColor(id) {
    return (window.Tomo && Tomo.avatarColor) ? Tomo.avatarColor(id) : 'var(--accent)';
  }

  function bubbleHtml(role, agentName, agentId) {
    const av = role === 'user' ? 'You' : esc((agentName || 'A').slice(0, 1).toUpperCase());
    const who = role === 'user' ? 'You' : esc(agentName || 'Agent');
    const style = role === 'assistant' && agentId ? ' style="background:' + agentColor(agentId) + '"' : '';
    return '<div class="msg ' + role + '"><div class="av"' + style + '>' + av + '</div><div class="bubble"><div class="who">' + who + '</div><div class="bubble-body prose chat-prose"></div></div></div>';
  }

  function highlightMentions(text) {
    return esc(text).replace(/@([a-zA-Z0-9_\-]+)/g, '<span class="mention-chip">@$1</span>');
  }

  function initChat(wrap) {
    if (!wrap || wrap.dataset.chatInit === '1') return;
    wrap.dataset.chatInit = '1';

    const agentId = wrap.dataset.agentId;
    const userId = wrap.dataset.userId || 'web';
    const scroll = wrap.querySelector('.chat-scroll');
    const input = wrap.querySelector('.chat-input');
    const sendBtn = wrap.querySelector('.chat-send');
    const clearBtn = wrap.querySelector('.chat-clear');
    const statusEl = wrap.querySelector('.chat-status');
    const mentionMenu = wrap.querySelector('.mention-menu');
    const defaultAgentName = wrap.dataset.agentName || (wrap.querySelector('.chat-agent-name') || {}).textContent || 'Agent';

    let mentionOpen = false;
    let mentionIndex = 0;
    let mentionMatches = [];
    let mentionRange = null; // {start, end} of @query in input

    function currentSessionId() { return wrap.dataset.sessionId || ''; }
    function pendingAgentIds() {
      return (wrap.dataset.pendingAgents || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    }

    function mentionableAgents() {
      // Prefer full catalog from sessions page; fall back to ids only.
      try {
        if (wrap.dataset.agentsJson) {
          const list = JSON.parse(wrap.dataset.agentsJson);
          if (Array.isArray(list) && list.length) return list;
        }
      } catch (e) {}
      const ids = (wrap.dataset.agentIds || wrap.dataset.pendingAgents || '')
        .split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      if (ids.length) {
        return ids.map(function (id) { return { id: id, name: id }; });
      }
      if (agentId) return [{ id: agentId, name: defaultAgentName || agentId }];
      return [];
    }

    function hideMentions() {
      mentionOpen = false;
      mentionMatches = [];
      mentionRange = null;
      if (mentionMenu) {
        mentionMenu.classList.add('hidden');
        mentionMenu.innerHTML = '';
      }
    }

    function renderMentionMenu() {
      if (!mentionMenu) return;
      if (!mentionMatches.length) {
        hideMentions();
        return;
      }
      mentionMenu.innerHTML = mentionMatches.map(function (a, i) {
        const active = i === mentionIndex ? ' active' : '';
        const letter = esc((a.name || a.id || '?').slice(0, 1).toUpperCase());
        const bg = agentColor(a.id);
        return '<button type="button" class="mention-item' + active + '" data-idx="' + i + '" role="option">' +
          '<span class="av" style="background:' + bg + '">' + letter + '</span>' +
          '<span class="meta"><span class="name">' + esc(a.name || a.id) + '</span>' +
          '<span class="id">@' + esc(a.id) + '</span></span></button>';
      }).join('');
      mentionMenu.classList.remove('hidden');
      mentionOpen = true;
      mentionMenu.querySelectorAll('.mention-item').forEach(function (btn) {
        btn.addEventListener('mousedown', function (e) {
          e.preventDefault();
          insertMention(parseInt(btn.dataset.idx, 10) || 0);
        });
      });
    }

    function filterMentions(query) {
      const q = (query || '').toLowerCase();
      const all = mentionableAgents().filter(function (a) {
        return a && a.id && a.enabled !== false;
      });
      if (!q) return all.slice(0, 12);
      return all.filter(function (a) {
        const id = String(a.id || '').toLowerCase();
        const name = String(a.name || '').toLowerCase();
        const role = String(a.role || '').toLowerCase();
        return id.indexOf(q) === 0 || name.indexOf(q) === 0 || role.indexOf(q) === 0 ||
          id.indexOf(q) >= 0 || name.indexOf(q) >= 0;
      }).slice(0, 12);
    }

    function detectMention() {
      if (!input) return null;
      const val = input.value;
      const caret = input.selectionStart != null ? input.selectionStart : val.length;
      const before = val.slice(0, caret);
      // Find last @ not preceded by word char (start or whitespace).
      const m = before.match(/(^|[\s([{])@([^\s@]*)$/);
      if (!m) return null;
      const query = m[2] || '';
      const start = caret - query.length - 1; // index of @
      return { start: start, end: caret, query: query };
    }

    function updateMentions() {
      if (!mentionMenu) return;
      const hit = detectMention();
      if (!hit) {
        hideMentions();
        return;
      }
      mentionRange = { start: hit.start, end: hit.end };
      mentionMatches = filterMentions(hit.query);
      mentionIndex = 0;
      renderMentionMenu();
    }

    function insertMention(idx) {
      if (!input || !mentionRange || !mentionMatches[idx]) return;
      const agent = mentionMatches[idx];
      const val = input.value;
      const before = val.slice(0, mentionRange.start);
      const after = val.slice(mentionRange.end);
      // Prefer @id for reliable server resolve; show name in UI via chip.
      const token = '@' + agent.id + ' ';
      input.value = before + token + after;
      const pos = before.length + token.length;
      input.setSelectionRange(pos, pos);
      hideMentions();
      input.focus();
      sendBtn.disabled = !input.value.trim() || sending;
      resize();
    }

    // Session chat may be a client-side draft (pendingAgents, no sessionId yet).
    if (!scroll || !input || !sendBtn || (!agentId && !currentSessionId() && !pendingAgentIds().length)) return;

    let sending = false, es = null;
    /** @type {{text: string, el: Element|null}[]} */
    let messageQueue = [];
    const MAX_QUEUE = 20;

    function atBottom() { scroll.scrollTop = scroll.scrollHeight; }
    function setStatus(badge, label) {
      if (!statusEl) return;
      statusEl.className = 'badge ' + badge;
      statusEl.innerHTML = '<span class="pulse"></span>' + esc(label);
    }

    function refreshSendBtn() {
      // While a turn is running, send is still allowed so messages enqueue.
      sendBtn.disabled = !input.value.trim();
    }

    function busyStatusLabel() {
      if (messageQueue.length) {
        return 'busy · ' + messageQueue.length + ' queued';
      }
      return 'busy';
    }

    function syncBusyStatus() {
      if (sending) setStatus('amber', busyStatusLabel());
      else if (!messageQueue.length) setStatus('ok', 'online');
    }

    scroll.querySelectorAll('.prose').forEach(renderMarkdown);
    atBottom();

    function resize() {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }

    function appendUserBubble(value, queued) {
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const u = document.createElement('div');
      u.innerHTML = bubbleHtml('user', defaultAgentName);
      const bubble = u.firstElementChild;
      bubble.querySelector('.bubble-body').innerHTML = highlightMentions(value);
      if (queued) {
        bubble.classList.add('msg-queued');
        const who = bubble.querySelector('.who');
        if (who) {
          who.innerHTML = 'You <span class="queue-chip">queued</span>';
        }
      }
      scroll.appendChild(bubble);
      atBottom();
      return bubble;
    }

    function markBubbleDequeued(el) {
      if (!el) return;
      el.classList.remove('msg-queued');
      const chip = el.querySelector('.queue-chip');
      if (chip) chip.remove();
    }

    function closeStream() {
      if (es) { es.close(); es = null; }
      // Do not clear sending here — finishTurn owns the queue drain.
    }

    function finishTurn() {
      if (es) { es.close(); es = null; }
      sending = false;
      Tomo.renderRail && Tomo.renderRail();
      wrap.dispatchEvent(new CustomEvent('tomo:chat-done'));
      if (messageQueue.length) {
        const next = messageQueue.shift();
        markBubbleDequeued(next.el);
        syncBusyStatus();
        // Fire-and-forget next turn (async).
        // Bubble was already shown when enqueued (or on the failed concurrent try).
        startTurn(next.text, { alreadyBubbled: true });
        return;
      }
      setStatus('ok', 'online');
      refreshSendBtn();
      input.focus();
    }

    /** Retry queued messages after a concurrent-session rejection (other tab). */
    function scheduleQueueDrain(delayMs) {
      setTimeout(function () {
        if (sending) return;
        if (!messageQueue.length) {
          setStatus('ok', 'online');
          return;
        }
        finishTurn();
      }, delayMs || 500);
    }

    function streamUrl(text) {
      const sid = currentSessionId();
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/stream?user_id=' + encodeURIComponent(userId) + '&message=' + encodeURIComponent(text);
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/stream?user_id=' + encodeURIComponent(userId) + '&message=' + encodeURIComponent(text);
    }

    function clearUrl() {
      const sid = currentSessionId();
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/clear';
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/clear?user_id=' + encodeURIComponent(userId);
    }

    async function ensureSession() {
      const existing = currentSessionId();
      if (existing) return existing;
      if (agentId) return '';
      const agents = pendingAgentIds();
      if (!agents.length) throw new Error('No agents');
      const data = await Tomo.api('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_ids: agents, user_id: userId }),
      });
      if (!data || !data.session_id) throw new Error('No session');
      wrap.dataset.sessionId = data.session_id;
      delete wrap.dataset.pendingAgents;
      wrap.dispatchEvent(new CustomEvent('tomo:session-created', {
        detail: { session_id: data.session_id, agent_ids: agents },
      }));
      return data.session_id;
    }

    function streamTurn(text) {
      // Clean up any leftover detail panel from a previous turn.
      var oldPanel = wrap.querySelector('.detail-panel');
      if (oldPanel) oldPanel.remove();

      const turn = document.createElement('div');
      turn.className = 'turn';
      scroll.appendChild(turn);
      let thinkEl = null, asstEl = null, asstBody = null, pendingEl = null, raw = '', closed = false;
      let turnAgentName = defaultAgentName;
      let turnAgentId = agentId || '';
      let turnActive = false;
      let sawDone = false;
      let idleTimer = null;
      let hardTimer = null;
      // Mid-turn stall (LLM hang after tool_result). Post-done wait for title.
      const IDLE_MS = 180000;
      const POST_DONE_MS = 20000;
      const HARD_MS = 720000;

      es = new EventSource(streamUrl(text));

      function clearWatchdogs() {
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
        if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
      }

      function armIdle(ms) {
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(function () {
          if (closed) return;
          console.warn('[tomo] turn idle timeout', sawDone ? 'post-done' : 'mid-turn');
          if (!sawDone && !(asstBody && (asstBody.textContent || '').trim())) {
            errorBubble('<span style="color:var(--danger)">Turn stalled (no response). You can send again.</span>');
          }
          endTurn();
        }, ms || IDLE_MS);
      }

      function bumpActivity() {
        armIdle(sawDone ? POST_DONE_MS : IDLE_MS);
      }

      hardTimer = setTimeout(function () {
        if (closed) return;
        console.warn('[tomo] turn hard timeout');
        errorBubble('<span style="color:var(--danger)">Turn timed out. You can send again.</span>');
        endTurn();
      }, HARD_MS);
      armIdle(IDLE_MS);

      // Log every wire event (browser console) for debugging streams / titles.
      [
        'state', 'turn.start', 'session', 'thinking', 'tool', 'tool_result',
        'delta', 'done', 'delegate', 'error', 'heartbeat', 'turn.end', 'auth_expired',
        'subagent_start', 'subagent_done',
      ].forEach(function (name) {
        es.addEventListener(name, function (e) {
          var payload = e && e.data;
          try { payload = JSON.parse(e.data || '{}'); } catch (_) {}
          console.log('[tomo sse]', name, payload);
        });
      });

      function clearPending() {
        if (pendingEl) { pendingEl.remove(); pendingEl = null; }
      }

      function showPending() {
        clearPending();
        pendingEl = document.createElement('div');
        pendingEl.className = 'turn-pending';
        const style = turnAgentId ? ' style="background:' + agentColor(turnAgentId) + '"' : '';
        pendingEl.innerHTML =
          '<div class="av"' + style + '>' + esc((turnAgentName || 'A').slice(0, 1).toUpperCase()) + '</div>' +
          '<div class="meta"><span class="name">' + esc(turnAgentName || 'Agent') + '</span>' +
          '<span class="typing" aria-hidden="true"><i></i><i></i><i></i></span></div>';
        turn.appendChild(pendingEl);
      }

      function ensureAssistantBubble() {
        clearPending();
        if (asstEl) return asstEl;
        const tmp = document.createElement('div');
        tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
        asstEl = tmp.firstElementChild;
        turn.appendChild(asstEl);
        asstBody = asstEl.querySelector('.bubble-body');
        return asstEl;
      }

      function dropEmptyAssistant() {
        if (!asstEl) return;
        const body = (asstBody && asstBody.textContent || '').trim();
        if (body) return;
        asstEl.remove();
        asstEl = null;
        asstBody = null;
        raw = '';
      }

      function errorBubble(bodyHtml) {
        clearPending();
        dropEmptyAssistant();
        const tmp = document.createElement('div');
        tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
        const b = tmp.firstElementChild;
        turn.appendChild(b);
        b.querySelector('.bubble-body').innerHTML = bodyHtml;
        atBottom();
      }

      function endTurn() {
        if (closed) return;
        clearWatchdogs();
        clearPending();
        dropEmptyAssistant();
        closeDetailPanel();
        closed = true;
        closeStream();
        finishTurn();
      }

      function adoptAgent(id, name) {
        var nextId = id || turnAgentId;
        var nextName = name || turnAgentName;
        var switched = (nextId && nextId !== turnAgentId) ||
          (nextName && nextName !== turnAgentName);
        if (nextId) turnAgentId = nextId;
        if (nextName) turnAgentName = nextName;
        if (switched) {
          clearPending();
          dropEmptyAssistant();
          asstEl = null;
          asstBody = null;
          raw = '';
        }
      }

      // ── Subagent tracking ──────────────────────────────────────────
      // Buffers accumulate events from delegated subagents so the detail
      // panel can replay them on demand.  subagentSet is a quick lookup
      // to decide whether an incoming event belongs to a subagent (skip
      // main-turn rendering) or to the parent (render normally).
      var subagentBuffers = new Map();
      var subagentSet = new Set();
      var swarmCard = null;
      var detailPanel = null;
      var activeDetailAgent = null;

      function getBuffer(aid) {
        if (!subagentBuffers.has(aid)) {
          subagentBuffers.set(aid, {
            events: [], status: 'running', task: '', name: '',
            index: 0, total: 1, row: null,
          });
        }
        return subagentBuffers.get(aid);
      }

      function bufferEvent(aid, kind, data) {
        var buf = getBuffer(aid);
        buf.events.push({ kind: kind, data: data });
        if (activeDetailAgent === aid && detailPanel) renderEventInDetail(kind, data, aid);
      }

      function isSubagentEvent(d) {
        var aid = d.agent_id || '';
        return aid && subagentSet.has(aid) && aid !== agentId;
      }

      function createSwarmCard() {
        clearPending();
        dropEmptyAssistant();
        if (swarmCard) return swarmCard;
        swarmCard = document.createElement('div');
        swarmCard.className = 'swarm-card';
        turn.appendChild(swarmCard);
        atBottom();
        return swarmCard;
      }

      function addSwarmRow(aid, name, task, idx, total) {
        var card = swarmCard || createSwarmCard();
        var row = document.createElement('div');
        row.className = 'swarm-row';
        row.dataset.agentId = aid;
        var color = agentColor(aid);
        var letter = esc((name || aid || '?').slice(0, 1).toUpperCase());
        var idxStr = String(idx).padStart(2, '0');
        var totalStr = String(total).padStart(2, '0');
        row.innerHTML =
          '<div class="av" style="background:' + color + '">' + letter + '</div>' +
          '<div class="swarm-meta">' +
            '<div class="swarm-row-head">' +
              '<span class="name">' + esc(name || aid) + '</span>' +
              '<span class="index">' + idxStr + ' / ' + totalStr + '</span>' +
            '</div>' +
            '<div class="task">' + esc(task || '') + '</div>' +
            '<div class="swarm-progress"><div class="swarm-progress-bar" style="width:0%"></div></div>' +
          '</div>';
        row.addEventListener('click', function () { openDetailPanel(aid); });
        card.appendChild(row);
        var buf = getBuffer(aid);
        buf.row = row;
        buf.name = name || aid;
        buf.task = task || '';
        buf.index = idx;
        buf.total = total;
        atBottom();
        return row;
      }

      function bumpSwarmProgress(aid) {
        var buf = subagentBuffers.get(aid);
        if (!buf || !buf.row) return;
        buf.row.classList.add('active');
        var bar = buf.row.querySelector('.swarm-progress-bar');
        if (bar) {
          var w = parseFloat(bar.style.width) || 0;
          bar.style.width = Math.min(92, w + 7) + '%';
        }
      }

      function markSwarmDone(aid, status) {
        var buf = subagentBuffers.get(aid);
        if (!buf) return;
        buf.status = status === 'error' ? 'error' : 'done';
        if (!buf.row) return;
        buf.row.classList.remove('active');
        buf.row.classList.add(buf.status);
        var bar = buf.row.querySelector('.swarm-progress-bar');
        if (bar) bar.style.width = '100%';
      }

      function openDetailPanel(aid) {
        activeDetailAgent = aid;
        if (!detailPanel) {
          detailPanel = document.createElement('div');
          detailPanel.className = 'detail-panel';
          wrap.appendChild(detailPanel);
        }
        detailPanel.innerHTML = '';
        var buf = subagentBuffers.get(aid) || getBuffer(aid);
        var name = buf.name || aid;
        var color = agentColor(aid);
        var letter = esc((name || '?').slice(0, 1).toUpperCase());

        var header = document.createElement('div');
        header.className = 'detail-header';
        header.innerHTML =
          '<button class="detail-close" type="button" title="Close">\u2715</button>' +
          '<div class="av" style="background:' + color + '">' + letter + '</div>' +
          '<div class="detail-meta">' +
            '<div class="name">' + esc(name) + '</div>' +
            '<div class="id mono">@' + esc(aid) + '</div>' +
          '</div>';
        detailPanel.appendChild(header);

        var body = document.createElement('div');
        body.className = 'detail-body';
        detailPanel.appendChild(body);

        // Footer with all agent chips.
        if (subagentBuffers.size > 0) {
          var footer = document.createElement('div');
          footer.className = 'detail-footer';
          subagentBuffers.forEach(function (b, id) {
            var chip = document.createElement('button');
            chip.className = 'detail-agent-chip' + (id === aid ? ' active' : '');
            chip.type = 'button';
            var cColor = agentColor(id);
            var cLetter = esc((b.name || id || '?').slice(0, 1).toUpperCase());
            chip.innerHTML =
              '<div class="av" style="background:' + cColor + '">' + cLetter + '</div>' +
              '<div class="label">' + esc(b.name || id) + '</div>';
            chip.addEventListener('click', function () { openDetailPanel(id); });
            footer.appendChild(chip);
          });
          detailPanel.appendChild(footer);
        }

        // Replay buffered events.
        buf.events.forEach(function (ev) {
          renderEventInDetail(ev.kind, ev.data, aid);
        });

        header.querySelector('.detail-close').addEventListener('click', closeDetailPanel);
        detailPanel.classList.add('open');
        requestAnimationFrame(function () { body.scrollTop = body.scrollHeight; });
      }

      function closeDetailPanel() {
        activeDetailAgent = null;
        if (!detailPanel) return;
        detailPanel.classList.remove('open');
        var panel = detailPanel;
        detailPanel = null;
        setTimeout(function () { if (panel) panel.remove(); }, 220);
      }

      function renderEventInDetail(kind, data, aid) {
        if (!detailPanel) return;
        var body = detailPanel.querySelector('.detail-body');
        if (!body) return;

        if (kind === 'thinking') {
          var el = document.createElement('div');
          el.className = 'thinking';
          el.textContent = data.content || '';
          body.appendChild(el);
        } else if (kind === 'tool') {
          var card = document.createElement('div');
          card.className = 'tool';
          card.innerHTML =
            '<div class="tool-head">' +
              '<span class="chevron">\u25B6</span>' +
              '<span class="tname">' + esc(data.tool || 'tool') + '</span> ' +
              '<span class="targs">' + esc(JSON.stringify(data.args || {})) + '</span>' +
            '</div>' +
            '<div class="tres" style="display:none"></div>';
          body.appendChild(card);
          card._res = card.querySelector('.tres');
          var head = card.querySelector('.tool-head');
          head.addEventListener('click', function () {
            card.classList.toggle('expanded');
            var ch = head.querySelector('.chevron');
            if (ch) ch.textContent = card.classList.contains('expanded') ? '\u25BC' : '\u25B6';
            if (card.classList.contains('expanded')) card._res.style.display = '';
          });
        } else if (kind === 'tool_result') {
          var cards = body.querySelectorAll('.tool');
          var last = cards[cards.length - 1];
          if (last && last._res) {
            last._res.textContent = (data.error ? '\u2717 ' : '\u2192 ') +
              (typeof data.result === 'string' ? data.result : JSON.stringify(data.result));
          }
        } else if (kind === 'delta' || kind === 'subagent_final') {
          var bubble = body.querySelector('.detail-assistant .bubble-body');
          if (!bubble) {
            var wrap2 = document.createElement('div');
            wrap2.className = 'msg assistant detail-assistant';
            var dColor = agentColor(aid);
            var dLetter = esc((subagentBuffers.get(aid) && subagentBuffers.get(aid).name || aid || '?').slice(0, 1).toUpperCase());
            wrap2.innerHTML =
              '<div class="av" style="background:' + dColor + '">' + dLetter + '</div>' +
              '<div class="bubble"><div class="bubble-body prose chat-prose"></div></div>';
            body.appendChild(wrap2);
            bubble = wrap2.querySelector('.bubble-body');
            wrap2._raw = '';
          }
          wrap2 = bubble.closest('.detail-assistant');
          wrap2._raw = (wrap2._raw || '') + (data.content || '');
          setMarkdown(bubble, wrap2._raw);
        }
        body.scrollTop = body.scrollHeight;
      }

      function makeToolCollapsible(card, d) {
        var head = card.querySelector(':scope > div:first-child') || card.firstElementChild;
        if (head) {
          head.classList.add('tool-head');
          head.style.cursor = 'pointer';
          var chevron = document.createElement('span');
          chevron.className = 'chevron';
          chevron.textContent = '\u25B6';
          head.insertBefore(chevron, head.firstChild);
          head.addEventListener('click', function (e) {
            if (e.target.closest('.targs')) return;
            card.classList.toggle('expanded');
            chevron.textContent = card.classList.contains('expanded') ? '\u25BC' : '\u25B6';
          });
        }
      }

      es.addEventListener('state', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (d.busy) {
          setStatus('amber', busyStatusLabel());
        }
        // Prefer busy=false to end; turn.end is the hard close from the server.
        if (!d.busy && turnActive) endTurn();
        else if (!d.busy && !sending) setStatus('ok', 'online');
      });
      es.addEventListener('delegate', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        var target = d.to || d.agent_id || '';
        var name = d.agent || target;
        var task = d.task || d.reason || '';
        var idx = d.parallel_index || 1;
        var total = d.parallel_total || 1;
        if (target) subagentSet.add(target);
        createSwarmCard();
        var buf = getBuffer(target);
        buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
        if (!buf.row) addSwarmRow(target, name, task, idx, total);
        setStatus('amber', 'busy \u00b7 ' + (total > 1 ? total + ' agents' : name));
        atBottom();
      });
      es.addEventListener('subagent_start', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        var aid = d.agent_id || '';
        var name = d.agent || aid;
        var task = d.task || '';
        var idx = d.parallel_index || 1;
        var total = d.parallel_total || 1;
        if (aid) subagentSet.add(aid);
        var buf = getBuffer(aid);
        buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
        if (!buf.row) addSwarmRow(aid, name, task, idx, total);
        buf.row.classList.add('active');
        atBottom();
      });
      es.addEventListener('subagent_done', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        var aid = d.agent_id || '';
        markSwarmDone(aid, d.status || 'ok');
        atBottom();
      });
      es.addEventListener('turn.start', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        turnActive = true;
        adoptAgent(d.agent_id, d.agent);
        if (!asstEl) showPending();
        atBottom();
      });
      es.addEventListener('session', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (!d.title) return;
        wrap.dispatchEvent(new CustomEvent('tomo:session-title', {
          detail: { session_id: d.session_id || currentSessionId(), title: d.title },
        }));
      });
      es.addEventListener('thinking', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (isSubagentEvent(d)) {
          bufferEvent(d.agent_id, 'thinking', d);
          bumpSwarmProgress(d.agent_id);
          return;
        }
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        if (!thinkEl) { thinkEl = document.createElement('div'); thinkEl.className = 'thinking'; turn.appendChild(thinkEl); }
        thinkEl.textContent += d.content || '';
        atBottom();
      });
      es.addEventListener('tool', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (isSubagentEvent(d)) {
          bufferEvent(d.agent_id, 'tool', d);
          bumpSwarmProgress(d.agent_id);
          return;
        }
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        dropEmptyAssistant();
        const card = document.createElement('div');
        card.className = 'tool';
        card.innerHTML = '<div><span class="tname">' + esc(d.tool || 'tool') + '</span> <span class="targs">' + esc(JSON.stringify(d.args || {})) + '</span></div><div class="tres" style="display:none"></div>';
        turn.appendChild(card);
        card._res = card.querySelector('.tres');
        makeToolCollapsible(card, d);
        atBottom();
      });
      es.addEventListener('tool_result', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (isSubagentEvent(d)) {
          bufferEvent(d.agent_id, 'tool_result', d);
          bumpSwarmProgress(d.agent_id);
          return;
        }
        const cards = turn.querySelectorAll('.tool');
        const last = cards[cards.length - 1];
        if (last && last._res) {
          var resultText = typeof d.result === 'string' ? d.result : JSON.stringify(d.result);
          var truncated = resultText.length > 300 ? resultText.slice(0, 300) + '\u2026' : resultText;
          last._res.textContent = (d.error ? '\u2717 ' : '\u2192 ') + truncated;
          last._res.style.display = '';
          if (d.error) {
            last.classList.add('expanded');
            var ch = last.querySelector('.chevron');
            if (ch) ch.textContent = '\u25BC';
          }
        }
        // Keep typing indicator after tool so UI does not look frozen mid-turn.
        if (!asstEl && !pendingEl) showPending();
        atBottom();
      });
      es.addEventListener('delta', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (isSubagentEvent(d)) {
          bufferEvent(d.agent_id, 'delta', d);
          bumpSwarmProgress(d.agent_id);
          return;
        }
        adoptAgent(d.agent_id, d.agent);
        const piece = d.content || '';
        // Drop internal swarm bookkeeping if a model echoes it mid-stream.
        if (!raw && /^\s*\[Swarm\]/.test(piece)) return;
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        ensureAssistantBubble();
        asstEl.classList.add('streaming');
        raw += piece;
        if (/^\s*\[Swarm\]/.test(raw)) {
          dropEmptyAssistant();
          raw = '';
          return;
        }
        setMarkdown(asstBody, raw);
        atBottom();
      });
      es.addEventListener('done', function (e) {
        bumpActivity();
        sawDone = true;
        armIdle(POST_DONE_MS);
        const d = JSON.parse(e.data || '{}');
        if (isSubagentEvent(d)) return;
        adoptAgent(d.agent_id, d.agent);
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        let content = (d.content != null ? String(d.content) : '').trim();
        if (content.indexOf('[Swarm]') === 0) content = '';
        if (content) {
          ensureAssistantBubble();
          raw = content;
          setMarkdown(asstBody, raw);
          asstEl.classList.remove('streaming');
        } else {
          clearPending();
          dropEmptyAssistant();
        }
        atBottom();
        setStatus('amber', busyStatusLabel());
      });
      es.addEventListener('turn.end', function () {
        // Server closed the turn cleanly (no forever-heartbeat after message).
        endTurn();
      });
      // Named SSE error vs transport close.
      es.addEventListener('error', function (e) {
        if (closed) return;
        if (e && e.data) {
          let msg = 'Agent error';
          let code = '';
          let errAgentId = '';
          try {
            const payload = JSON.parse(e.data);
            msg = payload.message || msg;
            code = payload.code || '';
            errAgentId = payload.agent_id || '';
          } catch (_) {}
          // Subagent errors don't end the parent turn.
          if (errAgentId && subagentSet.has(errAgentId) && errAgentId !== agentId) {
            bufferEvent(errAgentId, 'error', { message: msg });
            markSwarmDone(errAgentId, 'error');
            return;
          }
          if (code === 'session_busy' && text) {
            clearWatchdogs();
            closed = true;
            closeStream();
            messageQueue.unshift({ text: text, el: null });
            sending = false;
            setStatus('amber', busyStatusLabel());
            if (window.Tomo && Tomo.toast) {
              Tomo.toast('Session busy — message queued, retrying…', 'ok');
            }
            scheduleQueueDrain(700);
            return;
          }
          errorBubble('<span style="color:var(--danger)">' + esc(msg) + '</span>');
          endTurn();
          return;
        }
        // Transport close: after a normal turn.end/busy=false we already closed.
        // If the stream ends after activity, finish quietly — do not flash "interrupted".
        if (turnActive || sawDone) {
          endTurn();
          return;
        }
        errorBubble('<span style="color:var(--danger)">Stream interrupted</span>');
        endTurn();
      });
      es.addEventListener('heartbeat', function () {
        // Legacy: heartbeat after a message turn meant the turn generator finished.
        if (turnActive) endTurn();
      });
      es.addEventListener('auth_expired', function () { window.location.href = '/login'; });
    }

    /**
     * @param {string} value
     * @param {{alreadyBubbled?: boolean}} [opts]
     */
    async function startTurn(value, opts) {
      opts = opts || {};
      if (!value) return;
      if (sending) {
        // Nested call should not happen; guard for safety.
        enqueueMessage(value);
        return;
      }
      sending = true;
      refreshSendBtn();
      setStatus('amber', busyStatusLabel());
      try {
        await ensureSession();
      } catch (e) {
        sending = false;
        refreshSendBtn();
        setStatus('ok', 'online');
        Tomo.toast((e && e.message) || 'Could not start chat', 'err');
        // If ensure failed for a dequeued item, keep remaining queue usable.
        if (messageQueue.length) finishTurn();
        return;
      }
      if (!opts.alreadyBubbled) {
        appendUserBubble(value, false);
      }
      streamTurn(value);
    }

    function enqueueMessage(value) {
      if (messageQueue.length >= MAX_QUEUE) {
        Tomo.toast('Queue full (max ' + MAX_QUEUE + ' messages). Wait for the current turn.', 'err');
        return false;
      }
      const el = appendUserBubble(value, true);
      messageQueue.push({ text: value, el: el });
      setStatus('amber', busyStatusLabel());
      return true;
    }

    async function send(text) {
      const value = (text != null ? String(text) : input.value).trim();
      if (!value) return;
      input.value = '';
      resize();
      refreshSendBtn();
      if (sending) {
        enqueueMessage(value);
        return;
      }
      await startTurn(value, {});
    }

    input.addEventListener('input', function () {
      refreshSendBtn();
      resize();
      updateMentions();
    });
    input.addEventListener('keydown', function (e) {
      if (mentionOpen && mentionMatches.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          mentionIndex = (mentionIndex + 1) % mentionMatches.length;
          renderMentionMenu();
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          mentionIndex = (mentionIndex - 1 + mentionMatches.length) % mentionMatches.length;
          renderMentionMenu();
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          insertMention(mentionIndex);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          hideMentions();
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener('blur', function () {
      // Delay so mousedown on menu still fires.
      setTimeout(hideMentions, 150);
    });
    sendBtn.addEventListener('click', function () { send(); });
    resize();

    if (clearBtn) {
      clearBtn.addEventListener('click', async function () {
        if (!confirm('Clear this conversation?')) return;
        messageQueue = [];
        if (es) { es.close(); es = null; }
        sending = false;
        if (!currentSessionId() && !agentId) {
          wrap.dispatchEvent(new CustomEvent('tomo:chat-cleared'));
          return;
        }
        try {
          await Tomo.api(clearUrl(), { method: 'POST' });
          wrap.dispatchEvent(new CustomEvent('tomo:chat-cleared'));
        } catch (e) {
          Tomo.toast('Could not clear', 'err');
        }
      });
    }

    wrap.querySelectorAll('.chat-tab').forEach(function (t) {
      t.addEventListener('click', function () {
        wrap.querySelectorAll('.chat-tab').forEach(function (x) { x.classList.remove('active'); });
        t.classList.add('active');
        const chat = wrap.querySelector('[data-chat-panel="chat"]');
        const det = wrap.querySelector('[data-chat-panel="details"]');
        const isChat = t.dataset.tab === 'chat';
        if (chat) chat.style.display = isChat ? 'block' : 'none';
        if (det) det.style.display = isChat ? 'none' : 'block';
      });
    });

    return {
      destroy: function () {
        messageQueue = [];
        if (es) { es.close(); es = null; }
        sending = false;
      },
      send: send,
    };
  }

  window.TomoChat = { init: initChat, renderMarkdown: renderMarkdown, setMarkdown: setMarkdown };

  document.querySelectorAll('.chat-wrap').forEach(function (wrap) {
    initChat(wrap);
  });
})();

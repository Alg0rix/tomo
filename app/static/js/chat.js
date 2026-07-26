/* chat.js — streaming chat client (agent detail + swarm sessions page).
 * Event protocol: state · turn.start · session · thinking · tool · tool_result · delta · done · delegate · error · heartbeat
 */
(function () {
  "use strict";

  function esc(s) { return Tomo.escapeHtml(s); }
  function md(text) {
    if (typeof marked === "undefined") return esc(text).replace(/\n/g, '<br>');
    try { return marked.parse(text, { breaks: true, gfm: true }); } catch (e) { return esc(text); }
  }
  function renderCode(root) {
    if (!window.hljs) return;
    root.querySelectorAll('pre code').forEach(function (b) { try { hljs.highlightElement(b); } catch (e) {} });
  }
  function renderMarkdown(el) {
    // Idempotent: textContent of already-parsed HTML drops markdown markers,
    // so a second pass (e.g. history render then TomoChat.init) would flatten
    // formatting to plain text on refresh.
    if (!el || el.dataset.md === '1') return;
    el.innerHTML = md(el.textContent);
    renderCode(el);
    el.dataset.md = '1';
  }

  function setMarkdown(el, text) {
    if (!el) return;
    el.dataset.md = '0';
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
    return '<div class="msg ' + role + '"><div class="av"' + style + '>' + av + '</div><div class="bubble"><div class="who">' + who + '</div><div class="bubble-body prose"></div></div></div>';
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
      const turn = document.createElement('div');
      turn.className = 'turn';
      scroll.appendChild(turn);
      let thinkEl = null, asstEl = null, asstBody = null, pendingEl = null, raw = '', closed = false;
      let turnAgentName = defaultAgentName;
      let turnAgentId = agentId || '';
      let turnActive = false;

      es = new EventSource(streamUrl(text));

      // Log every wire event (browser console) for debugging streams / titles.
      [
        'state', 'turn.start', 'session', 'thinking', 'tool', 'tool_result',
        'delta', 'done', 'delegate', 'error', 'heartbeat', 'auth_expired',
      ].forEach(function (name) {
        es.addEventListener(name, function (e) {
          var payload = e && e.data;
          try { payload = JSON.parse(e.data || '{}'); } catch (_) {}
          console.log('[tomo sse]', name, payload);
        });
      });

      function close() {
        if (closed) return;
        closed = true;
        closeStream();
      }

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

      // Render an assistant bubble whose body is raw HTML (used for error text).
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
        clearPending();
        dropEmptyAssistant();
        closed = true;
        closeStream();
        finishTurn();
      }

      es.addEventListener('state', function (e) {
        const d = JSON.parse(e.data || '{}');
        if (d.busy) {
          setStatus('amber', busyStatusLabel());
        }
        // Close only after a turn started and busy clears — keeps the stream
        // open past `done` so the LLM session-title event is received.
        // Do not flip to "online" here if more messages are queued.
        if (!d.busy && turnActive) endTurn();
        else if (!d.busy && !sending) setStatus('ok', 'online');
      });
      function adoptAgent(id, name) {
        var nextId = id || turnAgentId;
        var nextName = name || turnAgentName;
        var switched = (nextId && nextId !== turnAgentId) ||
          (nextName && nextName !== turnAgentName);
        if (nextId) turnAgentId = nextId;
        if (nextName) turnAgentName = nextName;
        if (switched) {
          // New agent after handoff — do not keep coordinator's empty bubble.
          clearPending();
          dropEmptyAssistant();
          asstEl = null;
          asstBody = null;
          raw = '';
        }
      }

      es.addEventListener('delegate', function (e) {
        const d = JSON.parse(e.data || '{}');
        clearPending();
        dropEmptyAssistant();
        const row = document.createElement('div');
        row.className = 'delegate-line';
        row.textContent = d.content || ('Handing off to ' + (d.agent || d.to || d.agent_id));
        turn.appendChild(row);
        // Switch stream identity to the target so following deltas show Ops, not Tomo.
        adoptAgent(d.to || d.agent_id, d.agent);
        setStatus('amber', 'busy · ' + (d.agent || d.to || 'agent'));
        atBottom();
      });
      es.addEventListener('turn.start', function (e) {
        const d = JSON.parse(e.data || '{}');
        turnActive = true;
        adoptAgent(d.agent_id, d.agent);
        // Compact pending chip only — never an empty purple message slab.
        // Real bubbles appear on the first text delta / non-empty done.
        if (!asstEl) showPending();
        atBottom();
      });
      es.addEventListener('session', function (e) {
        const d = JSON.parse(e.data || '{}');
        if (!d.title) return;
        wrap.dispatchEvent(new CustomEvent('tomo:session-title', {
          detail: { session_id: d.session_id || currentSessionId(), title: d.title },
        }));
      });
      es.addEventListener('thinking', function (e) {
        const d = JSON.parse(e.data || '{}');
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        if (!thinkEl) { thinkEl = document.createElement('div'); thinkEl.className = 'thinking'; turn.appendChild(thinkEl); }
        thinkEl.textContent += d.content || '';
        atBottom();
      });
      es.addEventListener('tool', function (e) {
        const d = JSON.parse(e.data || '{}');
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        dropEmptyAssistant();
        const card = document.createElement('div');
        card.className = 'tool';
        card.innerHTML = '<div><span class="tname">' + esc(d.tool || 'tool') + '</span> <span class="targs">' + esc(JSON.stringify(d.args || {})) + '</span></div><div class="tres" style="display:none"></div>';
        turn.appendChild(card);
        card._res = card.querySelector('.tres');
        atBottom();
      });
      es.addEventListener('tool_result', function (e) {
        const d = JSON.parse(e.data || '{}');
        const cards = turn.querySelectorAll('.tool');
        const last = cards[cards.length - 1];
        if (last && last._res) {
          last._res.style.display = '';
          last._res.textContent = (d.error ? '✗ ' : '→ ') + (typeof d.result === 'string' ? d.result : JSON.stringify(d.result));
        }
        atBottom();
      });
      es.addEventListener('delta', function (e) {
        const d = JSON.parse(e.data || '{}');
        adoptAgent(d.agent_id, d.agent);
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        ensureAssistantBubble();
        asstEl.classList.add('streaming');
        raw += d.content || '';
        setMarkdown(asstBody, raw);
        atBottom();
      });
      es.addEventListener('done', function (e) {
        const d = JSON.parse(e.data || '{}');
        adoptAgent(d.agent_id, d.agent);
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        const content = (d.content != null ? String(d.content) : '').trim();
        if (content) {
          ensureAssistantBubble();
          raw = d.content;
          setMarkdown(asstBody, raw);
          asstEl.classList.remove('streaming');
        } else {
          clearPending();
          dropEmptyAssistant();
        }
        atBottom();
        // Stay "busy" until trailing state busy=false (title upgrade may still run).
        setStatus('amber', busyStatusLabel());
        // Do not close here — wait for trailing state busy=false so the LLM
        // session-title event (emitted after done) is still received.
      });
      // The 'error' listener fires for TWO distinct cases:
      //  (1) a named SSE event `event: error\ndata: {"message": ...}` — a server
      //      agent/loop error surfaced as a MessageEvent with `e.data` set. Show
      //      the server `message` as an agent error bubble and close cleanly; the
      //      server has already cleared busy and emits a trailing busy=false state.
      //  (2) a transport failure (network drop / es.close()) — an Event with NO
      //      `e.data` (es.readyState === CLOSED). Only this is "Stream interrupted".
      // Conflating (1) with (2) mislabels agent errors as broken connections and
      // can leave the busy badge stuck (the trailing busy=false state is dropped).
      es.addEventListener('error', function (e) {
        if (closed) return;
        if (e && e.data) {
          let msg = 'Agent error';
          let code = '';
          try {
            const payload = JSON.parse(e.data);
            msg = payload.message || msg;
            code = payload.code || '';
          } catch (_) {}
          closed = true;
          closeStream();
          // Rare concurrent turn (another tab). Re-queue and retry shortly —
          // do not finishTurn immediately or we thrash the session lock.
          if (code === 'session_busy' && text) {
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
          finishTurn();
          return;
        }
        errorBubble('<span style="color:var(--danger)">Stream interrupted</span>');
        closed = true;
        closeStream();
        finishTurn();
      });
      es.addEventListener('heartbeat', function () {});
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

/* chat_turn_stream.js — unified live + resume SSE turn handler */
(function () {
  "use strict";

  function attach(es, ctx) {
    var mode = ctx.mode;
    var isLive = mode === 'live';
    var closed = false;
    var sawDone = false;
    var sawTurnEvent = false;
    // Live starts caught-up. Resume waits for server ``caught_up`` (end of
    // replay) so new deltas/tools stream instead of being treated as history.
    var liveCaughtUp = isLive;
    var turnActive = false;
    var turnAgentName = ctx.defaultAgentName;
    var turnAgentId = ctx.agentId || '';
    var thinkEl = null;
    var asstEl = null;
    var asstBody = null;
    var pendingEl = null;
    var raw = '';
    var idleTimer = null;
    var reconnectTimer = null;
    var reconnectAttempts = 0;

    // Idle only: no wall-clock hard cap. Long subagent turns can run far past
    // 12 minutes; heartbeats reset the idle timer. Stale streams reconnect.
    var IDLE_MS = 180000;
    var POST_DONE_MS = 20000;
    var MAX_RECONNECT = 30;

    var skipTools = 0;
    var skipResults = 0;
    var skipThinking = 0;
    var skipUi = 0;
    var toolSeen = 0;
    var resultSeen = 0;
    var thinkingSeen = 0;
    var uiSeen = 0;

    if (!isLive) {
      skipTools = ctx.turn.querySelectorAll('.tool').length;
      ctx.turn.querySelectorAll('.tool').forEach(function (c) {
        if (c._res && c._res.textContent) skipResults++;
      });
      skipThinking = ctx.turn.querySelectorAll('.si-think, .reasoning-card').length;
      skipUi = 0;
      ctx.turn.querySelectorAll('.gen-ui[data-ui-id]').forEach(function (root) {
        skipUi += Number(root.dataset.uiEvents || 1) || 1;
      });
    }

    var subagentSet = new Set();
    // Always buffer subagent activity so refresh/resume can fill the inspector
    // for events that arrive after reconnect (not only pure live turns).
    var subagentBuffers = new Map();
    var swarmCard = null;
    var detailPanel = null;
    var activeDetailAgent = null;
    // Live fallback when wire has no delegate_call_id yet (pre-reload server):
    // allocate a unique key per delegate so same catalog agent gets N cards.
    var liveDelegateSeq = 0;
    var agentToInstanceKey = {};      // agent_id → latest instance key
    var parallelSlotKey = {};         // agent_id + ':' + parallel_index → key

    if (!isLive) {
      ctx.turn.querySelectorAll('.swarm-row[data-agent-id]').forEach(function (row) {
        if (row.dataset.agentId) subagentSet.add(row.dataset.agentId);
      });
    }

    function clearWatchdogs() {
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    }

    function markCaughtUp() {
      if (liveCaughtUp) return;
      liveCaughtUp = true;
      // Freeze skip counters at what we already saw during replay; anything
      // after this boundary is live activity and must render.
      skipTools = toolSeen;
      skipResults = resultSeen;
      skipThinking = thinkingSeen;
      skipUi = uiSeen;
    }

    /** True while resume is still replaying buffered history (skip mode). */
    function inReplaySkip() {
      return !isLive && !liveCaughtUp;
    }

    /** Rejoin listen SSE without finishing the turn (refresh / proxy drop / idle). */
    function tryReconnect(reason) {
      if (closed) return false;
      if (typeof ctx.reconnectStream !== 'function') return false;
      if (reconnectAttempts >= MAX_RECONNECT) {
        console.warn('[tomo] reconnect exhausted', reason);
        return false;
      }
      reconnectAttempts += 1;
      var delay = Math.min(800 * reconnectAttempts, 5000);
      console.warn('[tomo] stream reconnect', reason, 'attempt', reconnectAttempts, 'in', delay + 'ms');
      clearWatchdogs();
      closed = true;
      ctx.closeStream();
      // Keep busy UI; do not finishTurn — background agent is still running.
      ctx.setSending(true);
      ctx.wrap.dataset.liveStream = '1';
      ctx.setStatus('amber', ctx.busyStatusLabel());
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        try {
          ctx.reconnectStream(ctx.turn);
        } catch (err) {
          console.error('[tomo] reconnect failed', err);
          ctx.setSending(false);
          delete ctx.wrap.dataset.liveStream;
          ctx.setStatus('ok', 'online');
          ctx.refreshSendBtn();
        }
      }, delay);
      return true;
    }

    function armIdle(ms) {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(function () {
        if (closed) return;
        if (!isLive && !sawTurnEvent) {
          endIdleResume();
          return;
        }
        // Mid-turn silence usually means the SSE pipe died while the agent
        // keeps running — rejoin listen instead of "timed out".
        if (turnActive || sawTurnEvent || sawDone) {
          console.warn('[tomo] turn idle timeout', sawDone ? 'post-done' : 'mid-turn');
          if (tryReconnect(sawDone ? 'idle-post-done' : 'idle-mid-turn')) return;
        }
        if (isLive && !sawDone && !(asstBody && (asstBody.textContent || '').trim())) {
          errorBubble('<span style="color:var(--danger)">Turn stalled (no response). You can send again.</span>');
        }
        endTurn();
      }, ms || IDLE_MS);
    }

    function bumpActivity() {
      armIdle(isLive && sawDone ? POST_DONE_MS : IDLE_MS);
    }

    function clearPending() {
      if (pendingEl) { pendingEl.remove(); pendingEl = null; }
    }

    function showPending() {
      clearPending();
      pendingEl = document.createElement('div');
      pendingEl.className = 'turn-pending';
      var style = turnAgentId ? ' style="background:' + ctx.agentColor(turnAgentId) + '"' : '';
      pendingEl.innerHTML =
        '<div class="av"' + style + '>' + ctx.esc((turnAgentName || 'A').slice(0, 1).toUpperCase()) + '</div>' +
        '<div class="meta"><span class="name">' + ctx.esc(turnAgentName || 'Agent') + '</span>' +
        '<span class="typing" aria-hidden="true"><i></i><i></i><i></i></span></div>';
      ctx.turn.appendChild(pendingEl);
    }

    function ensureAssistantBubble() {
      clearPending();
      if (asstEl) return asstEl;
      // Always create a fresh bubble — never reuse a history bubble. History
      // already has completed segments rendered; new text must never merge
      // into them. (Resume reuse previously merged the final answer into a
      // trailing history bubble, which is exactly what caused duplicates.)
      var tmp = document.createElement('div');
      tmp.innerHTML = ctx.bubbleHtml('assistant', turnAgentName, turnAgentId);
      asstEl = tmp.firstElementChild;
      // Keep assistant intro ABOVE the Interactive hero when gen-ui already mounted.
      var hero = ctx.turn.querySelector('.gen-ui-block');
      if (hero) ctx.turn.insertBefore(asstEl, hero);
      else ctx.turn.appendChild(asstEl);
      asstBody = asstEl.querySelector('.bubble-body');
      return asstEl;
    }

    function dropEmptyAssistant() {
      if (!asstEl) return;
      var body = (asstBody && asstBody.textContent || '').trim();
      if (body) return;
      asstEl.remove();
      asstEl = null;
      asstBody = null;
      raw = '';
    }

    // "Commit" the current text segment so the next text creates a NEW bubble
    // after whatever comes next (tools). Empty bubbles are removed; otherwise
    // the streaming class is dropped. Always nulls out the active bubble refs.
    function sealAssistantBubble() {
      if (!asstEl) return;
      var body = (asstBody && asstBody.textContent || '').trim();
      if (!body) {
        asstEl.remove();
      } else {
        asstEl.classList.remove('streaming');
      }
      asstEl = null;
      asstBody = null;
      raw = '';
    }

    function appendReasoningCard(content) {
      if (window.Tomo && Tomo.buildReasoningCard) {
        ctx.turn.appendChild(Tomo.buildReasoningCard(content));
        return;
      }
      var details = document.createElement('details');
      details.className = 'reasoning-card';
      details.innerHTML = '<summary>Reasoning</summary><pre></pre>';
      details.querySelector('pre').textContent = content;
      ctx.turn.appendChild(details);
    }

    function collapseRenderUiTools() {
      ctx.turn.querySelectorAll('.tool[data-tool-name="render_ui"]').forEach(function (card) {
        card.classList.remove('expanded');
        card.classList.add('is-ui-ledger');
        if (card._head) card._head.setAttribute('aria-expanded', 'false');
        if (card._chip && !card.classList.contains('error')) {
          card._chip.textContent = 'rendered';
          card._chip.classList.remove('err');
        }
      });
    }

    function placeGenUiAsHero() {
      // Order: … tools … assistant text … Interactive hero.
      var blocks = ctx.turn.querySelectorAll('.gen-ui-block');
      if (!blocks.length) return;
      var msgs = ctx.turn.querySelectorAll('.msg.assistant');
      var lastAsst = msgs.length ? msgs[msgs.length - 1] : null;
      var anchor = lastAsst ? lastAsst.nextSibling : null;
      // Skip over other gen-ui-blocks when computing insert point.
      while (anchor && anchor.classList && anchor.classList.contains('gen-ui-block')) {
        anchor = anchor.nextSibling;
      }
      Array.prototype.forEach.call(blocks, function (block) {
        if (anchor) ctx.turn.insertBefore(block, anchor);
        else ctx.turn.appendChild(block);
      });
    }

    function appendGenerativeUI(spec) {
      if (!spec || !spec.ui_id || (!spec.tree && !spec.patch)) return null;
      if (!window.TomoGenerativeUI) {
        console.warn('[tomo] TomoGenerativeUI missing — generative_ui.js not loaded');
        return null;
      }
      var sendAction = function (action) {
        var body = '[UI action]\n' + JSON.stringify(action);
        if (typeof ctx.sendMessage === 'function') return ctx.sendMessage(body);
        return null;
      };
      clearPending();
      // Do NOT seal the assistant bubble — intro text should stay above the hero.
      collapseRenderUiTools();
      var mounted = TomoGenerativeUI.mount(ctx.turn, spec, {
        dispatch: ctx.dispatchUiAction || sendAction,
        sessionId: ctx.currentSessionId ? ctx.currentSessionId() : '',
        asBlock: true,
      });
      if (mounted) {
        var block = mounted.closest
          ? (mounted.closest('.gen-ui-block') || mounted)
          : mounted;
        block.classList.add('is-hero');
        ctx.turn.appendChild(block);
        placeGenUiAsHero();
      }
      return mounted;
    }

    function tryMountRenderUiFromResult(d) {
      if (d && d.error) return null;
      var toolName = ((d && (d.tool || d.name)) || '').toString();
      var raw = typeof (d && d.result) === 'string' ? d.result : '';
      if (toolName !== 'render_ui') return null;
      var spec = null;
      try { spec = JSON.parse(raw); } catch (_) { return null; }
      if (!spec || !spec.ui_id || (!spec.tree && !spec.patch)) return null;
      return appendGenerativeUI(spec);
    }

    function errorBubble(bodyHtml) {
      clearPending();
      dropEmptyAssistant();
      var tmp = document.createElement('div');
      tmp.innerHTML = ctx.bubbleHtml('assistant', turnAgentName, turnAgentId);
      var b = tmp.firstElementChild;
      ctx.turn.appendChild(b);
      b.querySelector('.bubble-body').innerHTML = bodyHtml;
      ctx.atBottom();
    }

    function endTurn() {
      if (closed) return;
      clearWatchdogs();
      clearPending();
      dropEmptyAssistant();
      closed = true;
      ctx.closeStream();
      delete ctx.wrap.dataset.liveStream;
      ctx.wrap.dispatchEvent(new CustomEvent('tomo:turn-end', { bubbles: true }));
      ctx.finishTurn();
    }

    function endIdleResume() {
      if (closed) return;
      clearWatchdogs();
      // Idle resume: drop spinners so cards don't stick on "running" forever.
      ctx.turn.querySelectorAll('.tool.loading, .tool.running, .si-tool.running').forEach(function (c) {
        c.classList.remove('loading');
        c.classList.remove('running');
        if (!c.classList.contains('ok') && !c.classList.contains('error') && !c.classList.contains('is-error')) {
          c.classList.add('ok');
        }
      });
      closed = true;
      ctx.closeStream();
      delete ctx.wrap.dataset.liveStream;
      ctx.setSending(false);
      ctx.setStatus('ok', 'online');
      ctx.refreshSendBtn();
    }

    function adoptAgent(id, name) {
      var nextId = id || turnAgentId;
      var nextName = name || turnAgentName;
      if (isLive) {
        var switched = (nextId && nextId !== turnAgentId) ||
          (nextName && nextName !== turnAgentName);
        if (nextId) turnAgentId = nextId;
        if (nextName) turnAgentName = nextName;
        if (switched) {
          clearPending();
          sealAssistantBubble();
        }
      } else {
        if (id) turnAgentId = id;
        if (name) turnAgentName = name;
      }
    }

    // ── Subagent helpers ────────────────────────────────────────────

    function isSubagentEvent(d) {
      var aid = d.agent_id || '';
      return aid && subagentSet.has(aid) && aid !== ctx.agentId;
    }

    /** Per-delegation instance key (same catalog agent can run twice). */
    function instanceKeyFrom(d, fallbackAid) {
      var dcid = (d && (d.delegate_call_id || d.delegateCallId)) || '';
      if (dcid) return 'd:' + dcid;
      var aid = fallbackAid || (d && d.agent_id) || '';
      var idx = d && d.parallel_index;
      if (aid && idx != null && parallelSlotKey[aid + ':' + idx]) {
        return parallelSlotKey[aid + ':' + idx];
      }
      if (aid && agentToInstanceKey[aid]) return agentToInstanceKey[aid];
      return aid || '';
    }

    /** Allocate a stable key for a new handoff (always unique per delegate). */
    function allocateInstanceKey(d, aid) {
      var dcid = (d && (d.delegate_call_id || d.delegateCallId)) || '';
      var key;
      if (dcid) {
        key = 'd:' + dcid;
      } else {
        liveDelegateSeq++;
        var idx = d && d.parallel_index;
        var total = d && d.parallel_total;
        if (aid && idx != null && total > 1) {
          key = 'p:' + aid + ':' + idx + '/' + total;
        } else {
          key = 'live:' + liveDelegateSeq;
        }
      }
      if (aid) {
        agentToInstanceKey[aid] = key;
        if (d && d.parallel_index != null) {
          parallelSlotKey[aid + ':' + d.parallel_index] = key;
        }
      }
      return key;
    }

    function makeToolCollapsible(card) {
      if (window.Tomo && Tomo.wireToolCard) Tomo.wireToolCard(card);
    }

    function buildToolCard(d) {
      if (window.Tomo && Tomo.buildToolCard) {
        return Tomo.buildToolCard({
          tool: d.tool || 'tool',
          args: d.args || {},
          running: true,
          call_id: d.call_id || '',
        });
      }
      var tool = d.tool || 'tool';
      var card = document.createElement('div');
      card.className = 'tool loading';
      if (d.call_id) card.dataset.callId = d.call_id;
      card.dataset.toolName = tool;
      card.innerHTML =
        '<button type="button" class="tool-head">' +
          '<span class="tstatus"></span><span class="tname">' + ctx.esc(tool) + '</span> ' +
          '<span class="targs"></span><span class="tchip"></span><span class="chevron"></span>' +
        '</button><div class="tool-body"><pre class="tres"></pre></div>';
      card._res = card.querySelector('.tres');
      card._chip = card.querySelector('.tchip');
      makeToolCollapsible(card);
      return card;
    }

    function applyToolResult(d) {
      var last = window.Tomo && Tomo.findToolCard
        ? Tomo.findToolCard(ctx.turn, d)
        : null;
      if (!last) {
        var cards = ctx.turn.querySelectorAll('.tool.loading');
        last = cards[0] || ctx.turn.querySelectorAll('.tool')[ctx.turn.querySelectorAll('.tool').length - 1];
      }
      if (last) {
        var resultText = typeof d.result === 'string' ? d.result : JSON.stringify(d.result);
        if (window.Tomo && Tomo.finishToolCard) {
          Tomo.finishToolCard(last, resultText, !!d.error);
        } else if (last._res) {
          last._res.textContent = resultText;
          last.classList.remove('loading');
          last.classList.remove('running');
        }
        try {
          var toolName = (d.name || d.tool || '').toString();
          var parsedArt = window.TomoArtifacts
            ? TomoArtifacts.parseSaveResult(toolName, resultText)
            : null;
          if (!d.error && parsedArt) {
            ctx.turn.appendChild(TomoArtifacts.buildSavedCard(parsedArt));
            if (TomoArtifacts.maybeAutoOpen) TomoArtifacts.maybeAutoOpen(parsedArt);
          }
          // Mount interactive UI from render_ui tool_result (same reliability
          // path as artifacts) — do not rely solely on a separate SSE `ui` event.
          if (!d.error) {
            tryMountRenderUiFromResult(
              { tool: toolName, result: resultText, error: !!d.error }
            );
          }
        } catch (_) {}
      }
      if (Array.isArray(d.todos) && window.Tomo && Tomo.upsertTodoPanel) {
        Tomo.upsertTodoPanel(ctx.turn, d.todos);
      } else if (
        !d.error &&
        ((d.tool || d.name || '') + '') === 'todo' &&
        window.Tomo &&
        Tomo.parseTodosResult
      ) {
        var parsedTodos = Tomo.parseTodosResult(
          typeof d.result === 'string' ? d.result : ''
        );
        if (parsedTodos && parsedTodos.length) {
          Tomo.upsertTodoPanel(ctx.turn, parsedTodos);
        }
      }
      if (!asstEl && !pendingEl) showPending();
      ctx.atBottom();
      // Re-pin through artifact panel width transition and async preview/image growth.
      // If a long-lived stick is already active this just re-pins it (go) instead of
      // replacing it with a short-lived stick that would die mid-stream.
      if (window.Tomo && Tomo.nudgeScrollBottom && ctx.scroll) {
        Tomo.nudgeScrollBottom(ctx.scroll, { holdMs: 5000, times: [0, 100, 300, 800, 1600] });
      }
    }

    // ── Full-featured subagent infrastructure (live) ────────────────

    function getBuffer(key) {
      if (!subagentBuffers.has(key)) {
        subagentBuffers.set(key, {
          events: [], status: 'running', task: '', name: '',
          index: 0, total: 1, row: null, agentId: '', key: key,
        });
      }
      return subagentBuffers.get(key);
    }

    function bufferEvent(key, kind, data) {
      var buf = getBuffer(key);
      buf.events.push({ kind: kind, data: data });
      if (activeDetailAgent === key && detailPanel) renderEventInDetail(kind, data, key);
    }

    function createSwarmCard() {
      clearPending();
      dropEmptyAssistant();
      if (swarmCard) return swarmCard;
      swarmCard = document.createElement('div');
      swarmCard.className = 'swarm-card';
      ctx.turn.appendChild(swarmCard);
      ctx.atBottom();
      return swarmCard;
    }

    function addSwarmRow(key, aid, name, task, idx, total) {
      var card = swarmCard || createSwarmCard();
      var row = document.createElement('div');
      row.className = 'swarm-row';
      row.dataset.agentId = aid;
      row.dataset.instanceKey = key;
      var color = ctx.agentColor(aid);
      var letter = ctx.esc((name || aid || '?').slice(0, 1).toUpperCase());
      var idxStr = String(idx).padStart(2, '0');
      var totalStr = String(total).padStart(2, '0');
      row.innerHTML =
        '<div class="av" style="background:' + color + '">' + letter + '</div>' +
        '<div class="swarm-meta">' +
          '<div class="swarm-row-head">' +
            '<span class="name">' + ctx.esc(name || aid) + '</span>' +
            '<span class="index">' + idxStr + ' / ' + totalStr + '</span>' +
          '</div>' +
          '<div class="task">' + ctx.esc(task || '') + '</div>' +
          '<div class="swarm-progress"><div class="swarm-progress-bar" style="width:0%"></div></div>' +
        '</div>' +
        '<span class="si-open-hint" aria-hidden="true">inspect \u2192</span>';
      row.addEventListener('click', function () { openDetailPanel(key); });
      card.appendChild(row);
      var buf = getBuffer(key);
      buf.row = row;
      buf.name = name || aid;
      buf.agentId = aid;
      buf.task = task || '';
      buf.index = idx;
      buf.total = total;
      ctx.atBottom();
      return row;
    }

    function bumpSwarmProgressLive(key) {
      var buf = subagentBuffers.get(key);
      if (!buf || !buf.row) return;
      buf.row.classList.add('active');
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) {
        var w = parseFloat(bar.style.width) || 0;
        bar.style.width = Math.min(92, w + 7) + '%';
      }
    }

    function markSwarmDoneLive(key, status) {
      var buf = subagentBuffers.get(key);
      if (!buf) return;
      buf.status = status === 'error' ? 'error' : 'done';
      if (!buf.row) return;
      buf.row.classList.remove('active');
      buf.row.classList.add(buf.status);
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) bar.style.width = '100%';
      if (activeDetailAgent === key && detailPanel) {
        var badge = detailPanel.querySelector('.si-status');
        if (badge) {
          badge.className = 'si-status ' + buf.status;
          badge.textContent = buf.status;
        }
        detailPanel.querySelectorAll('.si-pill').forEach(function (pill) {
          var dot = pill.querySelector('.dot');
          if (dot && pill.classList.contains('active')) {
            dot.className = 'dot ' + buf.status;
          }
        });
      }
    }

    function openDetailPanel(key) {
      activeDetailAgent = key;
      var buf = subagentBuffers.get(key) || getBuffer(key);
      var aid = buf.agentId || key;
      var name = buf.name || aid;
      var color = ctx.agentColor(aid);
      var letter = ctx.esc((name || '?').slice(0, 1).toUpperCase());
      var status = buf.status || 'running';

      var panel = ctx.wrap.querySelector('.subagent-inspector');
      if (!panel) {
        panel = document.createElement('aside');
        panel.className = 'subagent-inspector';
        panel.setAttribute('role', 'complementary');
        panel.setAttribute('aria-label', 'Subagent inspector');
        ctx.wrap.appendChild(panel);
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
              '<span class="si-name">' + ctx.esc(name) + '</span>' +
              '<span class="si-status ' + ctx.esc(status) + '">' + ctx.esc(status) + '</span>' +
            '</div>' +
            '<div class="si-id">@' + ctx.esc(aid) + '</div>' +
          '</div>' +
        '</div>' +
        '<button class="si-close" type="button" title="Close" aria-label="Close inspector">\u2715</button>';
      panel.appendChild(head);

      var taskEl = document.createElement('div');
      taskEl.className = 'si-task';
      if (buf.task) {
        taskEl.innerHTML = '<span class="si-task-label">Task</span>' + ctx.esc(buf.task);
      }
      panel.appendChild(taskEl);

      var bufferList = [];
      subagentBuffers.forEach(function (b, id) { bufferList.push({ id: id, buf: b }); });
      if (bufferList.length > 1) {
        var nameCounts = {};
        bufferList.forEach(function (item) {
          var base = item.buf.name || item.buf.agentId || item.id;
          nameCounts[base] = (nameCounts[base] || 0) + 1;
        });
        var nameSeen = {};
        var switcher = document.createElement('nav');
        switcher.className = 'si-switcher';
        switcher.setAttribute('aria-label', 'Subagents in this turn');
        bufferList.forEach(function (item) {
          var id = item.id;
          var b = item.buf;
          var base = b.name || b.agentId || id;
          nameSeen[base] = (nameSeen[base] || 0) + 1;
          var pill = document.createElement('button');
          pill.type = 'button';
          pill.className = 'si-pill' + (id === key ? ' active' : '');
          var st = b.status || 'running';
          var cAid = b.agentId || id;
          var cColor = ctx.agentColor(cAid);
          var cLetter = ctx.esc((b.name || cAid || '?').slice(0, 1).toUpperCase());
          var label = base;
          if (nameCounts[base] > 1) label += ' #' + nameSeen[base];
          pill.innerHTML =
            '<span class="av" style="background:' + cColor + '">' + cLetter + '</span>' +
            '<span>' + ctx.esc(label) + '</span>' +
            '<span class="dot ' + ctx.esc(st) + '"></span>';
          pill.addEventListener('click', function () { openDetailPanel(id); });
          switcher.appendChild(pill);
        });
        panel.appendChild(switcher);
      }

      var body = document.createElement('div');
      body.className = 'si-body';
      panel.appendChild(body);

      if (!buf.events.length) {
        body.innerHTML = '<div class="si-empty">No steps yet \u2014 waiting for this agent to run.</div>';
      } else {
        var tl = document.createElement('div');
        tl.className = 'si-timeline';
        body.appendChild(tl);
        buf.events.forEach(function (ev) {
          renderEventInDetail(ev.kind, ev.data, key);
        });
      }

      head.querySelector('.si-close').addEventListener('click', closeDetailPanel);
      ctx.wrap.querySelectorAll('.swarm-row').forEach(function (r) {
        r.classList.toggle('selected', (r.dataset.instanceKey || r.dataset.agentId) === key);
      });
      requestAnimationFrame(function () { body.scrollTop = body.scrollHeight; });
    }

    function closeDetailPanel() {
      activeDetailAgent = null;
      ctx.wrap.querySelectorAll('.swarm-row.selected').forEach(function (r) {
        r.classList.remove('selected');
      });
      var panel = ctx.wrap.querySelector('.subagent-inspector');
      detailPanel = null;
      if (panel) panel.remove();
    }

    function renderEventInDetail(kind, data) {
      if (!detailPanel) return;
      var body = detailPanel.querySelector('.si-body');
      if (!body) return;
      if (window.Tomo && Tomo.renderInspectorStep) {
        Tomo.renderInspectorStep(body, kind, data);
        body.scrollTop = body.scrollHeight;
      }
    }

    // ── Simple swarm helpers (resume) ───────────────────────────────

    function swarmRowFor(key) {
      if (!key) return null;
      var rows = ctx.turn.querySelectorAll('.swarm-row');
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].dataset.instanceKey === key || rows[i].dataset.agentId === key) return rows[i];
      }
      return null;
    }

    function bumpSwarmProgressResume(key) {
      if (!key) return;
      var row = swarmRowFor(key);
      if (!row) return;
      row.classList.add('active');
      var bar = row.querySelector('.swarm-progress-bar');
      if (bar) {
        var w = parseFloat(bar.style.width) || 0;
        bar.style.width = Math.min(92, w + 7) + '%';
      }
    }

    function ensureSwarmRow(key, aid, name, task, idx, total) {
      if (!aid && !key) return;
      if (aid) subagentSet.add(aid);
      var row = swarmRowFor(key || aid);
      if (row) {
        bumpSwarmProgressResume(key || aid);
        return row;
      }
      var card = ctx.turn.querySelector('.swarm-card');
      if (!card) {
        card = document.createElement('div');
        card.className = 'swarm-card';
        ctx.turn.appendChild(card);
      }
      var instKey = key || aid;
      row = document.createElement('div');
      row.className = 'swarm-row active';
      row.dataset.agentId = aid;
      row.dataset.instanceKey = instKey;
      var color = ctx.agentColor(aid);
      var letter = ctx.esc((name || aid || '?').slice(0, 1).toUpperCase());
      var idxStr = String(idx || 1).padStart(2, '0');
      var totalStr = String(total || 1).padStart(2, '0');
      row.innerHTML =
        '<div class="av" style="background:' + color + '">' + letter + '</div>' +
        '<div class="swarm-meta">' +
          '<div class="swarm-row-head">' +
            '<span class="name">' + ctx.esc(name || aid) + '</span>' +
            '<span class="index">' + idxStr + ' / ' + totalStr + '</span>' +
          '</div>' +
          '<div class="task">' + ctx.esc(task || '') + '</div>' +
          '<div class="swarm-progress"><div class="swarm-progress-bar" style="width:8%"></div></div>' +
        '</div>' +
        '<span class="si-open-hint" aria-hidden="true">inspect \u2192</span>';
      card.appendChild(row);
      ctx.atBottom();
      return row;
    }

    function markSwarmDoneResume(key, status) {
      if (!key) return;
      var row = swarmRowFor(key);
      if (!row) return;
      row.classList.remove('active');
      row.classList.add(status === 'error' ? 'error' : 'done');
      var bar = row.querySelector('.swarm-progress-bar');
      if (bar) bar.style.width = '100%';
    }

    function bumpSwarmProgress(key) {
      if (isLive) bumpSwarmProgressLive(key);
      else bumpSwarmProgressResume(key);
    }

    function markSwarmDone(key, status) {
      if (isLive) markSwarmDoneLive(key, status);
      else markSwarmDoneResume(key, status);
    }

    // ── HITL ────────────────────────────────────────────────────────

    var hitlCb = function () {
      bumpActivity();
      clearPending();
    };
    if (ctx.onBindHitl) {
      ctx.onBindHitl(es, ctx.turn, hitlCb);
    } else if (window.TomoHitl && TomoHitl.bindStream) {
      TomoHitl.bindStream(es, {
        turn: ctx.turn,
        scroll: ctx.scroll,
        onEvent: hitlCb,
        clearPending: null,
        setBusy: function () { ctx.setStatus('amber', ctx.busyStatusLabel()); },
      });
    }

    // ── Wire SSE listeners ──────────────────────────────────────────

    es.addEventListener('state', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (isLive) {
        if (d.busy) {
          ctx.setStatus('amber', ctx.busyStatusLabel());
        }
        if (!d.busy && turnActive) {
          var who = d.agent_id || '';
          if (!who || who === turnAgentId || who === ctx.agentId) endTurn();
        } else if (!d.busy && !ctx.getSending()) {
          ctx.setStatus('ok', 'online');
        }
      } else {
        if (d.busy) ctx.setStatus('amber', ctx.busyStatusLabel());
      }
    });

    es.addEventListener('turn.start', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      turnActive = true;
      if (isLive) {
        adoptAgent(d.agent_id, d.agent);
        if (!asstEl) showPending();
      } else {
        sawTurnEvent = true;
        if (d.agent_id || d.agent) adoptAgent(d.agent_id, d.agent);
        ctx.setStatus('amber', ctx.busyStatusLabel());
      }
      ctx.atBottom();
    });

    if (isLive) {
      es.addEventListener('session', function (e) {
        bumpActivity();
        var d = JSON.parse(e.data || '{}');
        if (!d.title) return;
        ctx.wrap.dispatchEvent(new CustomEvent('tomo:session-title', {
          detail: { session_id: d.session_id || ctx.currentSessionId(), title: d.title },
        }));
      });
    }

    es.addEventListener('caught_up', function () {
      bumpActivity();
      markCaughtUp();
    });

    es.addEventListener('delegate', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var target = d.to || d.agent_id || '';
      var key = isLive || liveCaughtUp
        ? allocateInstanceKey(d, target)
        : instanceKeyFrom(d, target);
      // Resume without dcid: still try parallel slot / allocate locally.
      if ((!isLive || !key) && (!key || key === target)) {
        key = allocateInstanceKey(d, target);
      }
      var name = d.agent || target;
      var task = d.task || d.reason || '';
      var idx = d.parallel_index || 1;
      var total = d.parallel_total || 1;
      if (target) subagentSet.add(target);
      createSwarmCard();
      var buf = getBuffer(key);
      buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
      buf.agentId = target;
      // Reuse history-rendered swarm rows on resume so replay does not duplicate cards.
      if (!buf.row) buf.row = swarmRowFor(key) || swarmRowFor(target);
      if (!buf.row) addSwarmRow(key, target, name, task, idx, total);
      ctx.setStatus('amber', 'busy \u00b7 ' + (total > 1 ? total + ' agents' : name));
      ctx.atBottom();
    });

    es.addEventListener('subagent_start', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      var key = instanceKeyFrom(d, aid);
      // First sighting without a prior delegate: allocate a card.
      if (!key || !subagentBuffers.has(key)) {
        if (!key || key === aid) key = allocateInstanceKey(d, aid);
      }
      var name = d.agent || aid;
      var task = d.task || '';
      var idx = d.parallel_index || 1;
      var total = d.parallel_total || 1;
      if (aid) subagentSet.add(aid);
      var buf = getBuffer(key);
      buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
      buf.agentId = aid;
      if (!buf.row) buf.row = swarmRowFor(key) || swarmRowFor(aid);
      if (!buf.row) addSwarmRow(key, aid, name, task, idx, total);
      if (buf.row) buf.row.classList.add('active');
      ctx.atBottom();
    });

    es.addEventListener('subagent_done', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      markSwarmDone(instanceKeyFrom(d, d.agent_id || ''), d.status || 'ok');
      ctx.atBottom();
    });

    es.addEventListener('thinking', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        var ik = instanceKeyFrom(d, d.agent_id);
        bufferEvent(ik, 'thinking', d);
        bumpSwarmProgress(ik);
        return;
      }
      adoptAgent(d.agent_id, d.agent);
      clearPending();
      var content = d.content || '';
      if (!content.trim() || /^\s*\[Swarm\]/.test(content)) return;
      if (!isLive) {
        // Skip reasoning already in history until caught_up freezes skipThinking.
        thinkingSeen++;
        if (thinkingSeen <= skipThinking) return;
      }
      // Tool-call rounds may have streamed the model's reasoning as deltas
      // before the canonical `thinking` event arrives. Replace that temporary
      // bubble with one durable, collapsible reasoning card.
      if (asstEl) {
        asstEl.remove();
        asstEl = null;
        asstBody = null;
      }
      if (thinkEl) { thinkEl.remove(); thinkEl = null; }
      raw = '';
      appendReasoningCard(content);
      ctx.atBottom();
    });

    es.addEventListener('tool', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      if (aid && subagentSet.has(aid) && aid !== ctx.agentId) {
        var ik = instanceKeyFrom(d, aid);
        bufferEvent(ik, 'tool', d);
        bumpSwarmProgress(ik);
        return;
      }
      if (!isLive) {
        toolSeen++;
        if (toolSeen <= skipTools) {
          var cards = ctx.turn.querySelectorAll('.tool');
          var card = cards[toolSeen - 1];
          if (card && !(card._res && card._res.textContent)) {
            card.classList.add('loading');
            if (!card.querySelector('.tloading')) {
              var tip = document.createElement('div');
              tip.className = 'tloading';
              tip.textContent = 'running\u2026';
              card.insertBefore(tip, card._res || null);
            }
          }
          return;
        }
      }
      adoptAgent(d.agent_id, d.agent);
      clearPending();
      sealAssistantBubble();
      ctx.turn.appendChild(buildToolCard(d));
      ctx.atBottom();
    });

    es.addEventListener('tool_result', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      if (aid && subagentSet.has(aid) && aid !== ctx.agentId) {
        var ik = instanceKeyFrom(d, aid);
        bufferEvent(ik, 'tool_result', d);
        bumpSwarmProgress(ik);
        return;
      }
      if (!isLive) {
        resultSeen++;
        if (resultSeen <= skipResults) return;
      }
      applyToolResult(d);
    });

    es.addEventListener('ui', function (e) {
      bumpActivity();
      var d = null;
      try { d = JSON.parse(e.data || '{}'); } catch (err) {
        console.warn('[tomo] ui event JSON parse failed', err);
        return;
      }
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        var ik = instanceKeyFrom(d, d.agent_id);
        bufferEvent(ik, 'ui', d);
        bumpSwarmProgress(ik);
        return;
      }
      if (!isLive) {
        uiSeen++;
        if (uiSeen <= skipUi) return;
      }
      adoptAgent(d.agent_id, d.agent);
      appendGenerativeUI(d);
      ctx.atBottom();
    });

    es.addEventListener('todos', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      // Resume snapshot (source=resume) restores the dock without pinning mid-turn.
      if (!isLive && d.source !== 'resume') sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        var ik = instanceKeyFrom(d, d.agent_id);
        bufferEvent(ik, 'todos', d);
        bumpSwarmProgress(ik);
        return;
      }
      if (Array.isArray(d.todos) && window.Tomo && Tomo.upsertTodoPanel) {
        clearPending();
        Tomo.upsertTodoPanel(ctx.turn, d.todos);
        if (d.source !== 'resume' && !asstEl && !pendingEl) showPending();
        ctx.atBottom();
      }
    });

    es.addEventListener('delta', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        var ik = instanceKeyFrom(d, d.agent_id);
        bufferEvent(ik, 'delta', d);
        bumpSwarmProgress(ik);
        return;
      }
      if (inReplaySkip()) {
        // Replay of past deltas would duplicate history text. After
        // ``caught_up``, new live tokens stream normally below.
        return;
      }
      adoptAgent(d.agent_id, d.agent);
      var piece = d.content || '';
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
      ctx.setMarkdown(asstBody, raw);
      ctx.atBottom();
    });

    // Mid-turn steer: seal the current assistant segment so the next
    // deltas open a fresh bubble; turn continues (not turn.end).
    es.addEventListener('user', function (e) {
      bumpActivity();
      sawDone = false;
      sealAssistantBubble();
      if (thinkEl) { thinkEl.remove(); thinkEl = null; }
      try {
        var d = JSON.parse(e.data || '{}');
        if (d && d.steered && window.Tomo && Tomo.toast) {
          /* client already rendered the steered bubble */
        }
      } catch (_) {}
    });

    es.addEventListener('done', function (e) {
      bumpActivity();
      sawDone = true;
      if (isLive) {
        armIdle(POST_DONE_MS);
      }
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        var dik = instanceKeyFrom(d, d.agent_id);
        bufferEvent(dik, 'done', d);
        bumpSwarmProgress(dik);
        return;
      }
      adoptAgent(d.agent_id, d.agent);
      if (thinkEl) { thinkEl.remove(); thinkEl = null; }
      var content = (d.content != null ? String(d.content) : '').trim();
      if (content.indexOf('[Swarm]') === 0) content = '';
      if (!isLive && content) {
        // History may already contain the final assistant bubble (e.g. refresh
        // after a complete turn). If the last meaningful child is an assistant
        // bubble whose rendered content matches the final, it is already
        // rendered — skip to avoid a duplicate. Otherwise render fresh below.
        var kids = ctx.turn.children;
        for (var i = kids.length - 1; i >= 0; i--) {
          var c = kids[i];
          if (c.classList && c.classList.contains('turn-pending')) continue;
          if (c.classList && c.classList.contains('msg') && c.classList.contains('assistant')) {
            var body = c.querySelector('.bubble-body');
            var existing = (body && (body.dataset.raw || body.textContent) || '').trim();
            if (existing === content) return; // already rendered
          }
          break;
        }
      }
      if (content) {
        ensureAssistantBubble();
        raw = content;
        ctx.setMarkdown(asstBody, raw);
        asstEl.classList.remove('streaming');
        sealAssistantBubble();
      } else {
        clearPending();
        dropEmptyAssistant();
      }
      ctx.atBottom();
      placeGenUiAsHero();
      ctx.atBottom();
      ctx.setStatus('amber', ctx.busyStatusLabel());
    });

    es.addEventListener('turn.end', function (e) {
      try {
        var raw = e && e.data ? JSON.parse(e.data) : null;
        if (raw && raw.approval && typeof ctx.onApproval === 'function') {
          ctx.onApproval(raw.approval);
        }
      } catch (_) {}
      endTurn();
    });

    es.addEventListener('error', function (e) {
      if (closed) return;
      if (e && e.data) {
        var msg = 'Agent error';
        var code = '';
        var errAgentId = '';
        try {
          var payload = JSON.parse(e.data);
          msg = payload.message || msg;
          code = payload.code || '';
          errAgentId = payload.agent_id || '';
        } catch (_) {}
        if (errAgentId && subagentSet.has(errAgentId) && errAgentId !== ctx.agentId) {
          var errKey = instanceKeyFrom(payload, errAgentId);
          bufferEvent(errKey, 'error', { message: msg });
          markSwarmDone(errKey, 'error');
          return;
        }
        if (isLive && code === 'session_busy' && ctx.text) {
          clearWatchdogs();
          closed = true;
          ctx.closeStream();
          ctx.messageQueue.unshift({ text: ctx.text, el: null, attachmentIds: ctx.attachIds || [] });
          ctx.setSending(false);
          ctx.setStatus('amber', ctx.busyStatusLabel());
          if (window.Tomo && Tomo.toast) {
            Tomo.toast('Session busy \u2014 message queued, retrying\u2026', 'ok');
          }
          ctx.scheduleQueueDrain(700);
          return;
        }
        if (code === 'cancelled') {
          errorBubble('<span class="faint">' + ctx.esc(msg || 'Stopped') + '</span>');
          endTurn();
          return;
        }
        errorBubble('<span style="color:var(--danger)">' + ctx.esc(msg) + '</span>');
        endTurn();
        return;
      }
      if (isLive) {
        // Named agent error already handled above. Bare "error" is usually a
        // transport fault (fetch abort, proxy) — rejoin if the turn was live.
        if (turnActive || sawDone || sawTurnEvent) {
          if (tryReconnect('live-error')) return;
          endTurn();
          return;
        }
        errorBubble('<span style="color:var(--danger)">Stream interrupted</span>');
        endTurn();
      } else {
        // EventSource auto-retries the listen URL; keep the attachment if we
        // already know a turn is live. Only detach idle heartbeats.
        if (sawTurnEvent || turnActive) return;
        endIdleResume();
      }
    });

    es.addEventListener('stream_closed', function () {
      if (closed) return;
      // POST body ended without turn.end (proxy idle kill, tab sleep, etc.).
      if (turnActive || sawTurnEvent || sawDone) {
        if (tryReconnect('stream-closed')) return;
      }
      if (!sawDone) {
        errorBubble('<span style="color:var(--danger)">Stream interrupted</span>');
      }
      endTurn();
    });

    es.addEventListener('heartbeat', function () {
      if (sawDone) return;
      // Idle listen (no active turn) only emits heartbeats + busy:false state.
      // Active-turn listen always injects turn.start first, so sawTurnEvent is set.
      if (!isLive && !sawTurnEvent) {
        endIdleResume();
        return;
      }
      // Heartbeat means the server has finished replaying into the queue and
      // is waiting for new events — treat as caught-up if the marker was lost.
      if (!isLive) markCaughtUp();
      bumpActivity();
    });

    es.addEventListener('auth_expired', function () { window.location.href = '/login'; });

    // ── Resume-specific init and fallback ───────────────────────────

    if (!isLive) {
      ctx.setSending(true);
      ctx.wrap.dataset.liveStream = '1';
      ctx.setStatus('amber', ctx.busyStatusLabel());
      ctx.wrap.dispatchEvent(new CustomEvent('tomo:turn-start', { bubbles: true }));
      ctx.refreshSendBtn();
      armIdle(IDLE_MS);
      // Active-turn listen injects turn.start immediately. Idle listen only
      // heartbeats — the heartbeat handler ends resume when nothing is live.
    } else {
      armIdle(IDLE_MS);
    }

    return { end: endTurn };
  }

  window.TomoTurnStream = { attach: attach };
})();

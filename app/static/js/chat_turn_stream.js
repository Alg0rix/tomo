/* chat_turn_stream.js — unified live + resume SSE turn handler */
(function () {
  "use strict";

  function attach(es, ctx) {
    var mode = ctx.mode;
    var isLive = mode === 'live';
    var closed = false;
    var sawDone = false;
    var sawTurnEvent = false;
    var turnActive = false;
    var turnAgentName = ctx.defaultAgentName;
    var turnAgentId = ctx.agentId || '';
    var thinkEl = null;
    var asstEl = null;
    var asstBody = null;
    var pendingEl = null;
    var raw = '';
    var idleTimer = null;
    var hardTimer = null;

    var IDLE_MS = 180000;
    var POST_DONE_MS = 20000;
    var HARD_MS = 720000;

    var skipTools = 0;
    var skipResults = 0;
    var toolSeen = 0;
    var resultSeen = 0;

    if (!isLive) {
      skipTools = ctx.turn.querySelectorAll('.tool').length;
      ctx.turn.querySelectorAll('.tool').forEach(function (c) {
        if (c._res && c._res.textContent) skipResults++;
      });
    }

    var subagentSet = new Set();
    var subagentBuffers = isLive ? new Map() : null;
    var swarmCard = null;
    var detailPanel = null;
    var activeDetailAgent = null;

    if (!isLive) {
      ctx.turn.querySelectorAll('.swarm-row[data-agent-id]').forEach(function (row) {
        if (row.dataset.agentId) subagentSet.add(row.dataset.agentId);
      });
    }

    function clearWatchdogs() {
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
    }

    function armIdle(ms) {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(function () {
        if (closed) return;
        if (!isLive && !sawTurnEvent) {
          endIdleResume();
          return;
        }
        if (isLive) {
          console.warn('[tomo] turn idle timeout', sawDone ? 'post-done' : 'mid-turn');
          if (!sawDone && !(asstBody && (asstBody.textContent || '').trim())) {
            errorBubble('<span style="color:var(--danger)">Turn stalled (no response). You can send again.</span>');
          }
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
      if (!isLive) {
        var existing = ctx.turn.querySelector('.msg.assistant');
        if (existing) {
          asstEl = existing;
          asstBody = existing.querySelector('.bubble-body');
          raw = (asstBody && asstBody.textContent) || '';
          return asstEl;
        }
      }
      var tmp = document.createElement('div');
      tmp.innerHTML = ctx.bubbleHtml('assistant', turnAgentName, turnAgentId);
      asstEl = tmp.firstElementChild;
      ctx.turn.appendChild(asstEl);
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
      ctx.turn.querySelectorAll('.tool.loading').forEach(function (c) {
        c.classList.remove('loading');
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
          dropEmptyAssistant();
          asstEl = null;
          asstBody = null;
          raw = '';
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

    function makeToolCollapsible(card) {
      if (window.Tomo && Tomo.wireToolCard) Tomo.wireToolCard(card);
    }

    function buildToolCard(d) {
      if (window.Tomo && Tomo.buildToolCard) {
        return Tomo.buildToolCard({ tool: d.tool || 'tool', args: d.args || {}, running: true });
      }
      var tool = d.tool || 'tool';
      var card = document.createElement('div');
      card.className = 'tool loading';
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
      var cards = ctx.turn.querySelectorAll('.tool');
      var last = cards[cards.length - 1];
      if (last) {
        var resultText = typeof d.result === 'string' ? d.result : JSON.stringify(d.result);
        if (window.Tomo && Tomo.finishToolCard) {
          Tomo.finishToolCard(last, resultText, !!d.error);
        } else if (last._res) {
          last._res.textContent = resultText;
          last.classList.remove('loading');
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
        } catch (_) {}
      }
      if (Array.isArray(d.todos) && window.Tomo && Tomo.upsertTodoPanel) {
        Tomo.upsertTodoPanel(ctx.turn, d.todos);
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

    function addSwarmRow(aid, name, task, idx, total) {
      var card = swarmCard || createSwarmCard();
      var row = document.createElement('div');
      row.className = 'swarm-row';
      row.dataset.agentId = aid;
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
      row.addEventListener('click', function () { openDetailPanel(aid); });
      card.appendChild(row);
      var buf = getBuffer(aid);
      buf.row = row;
      buf.name = name || aid;
      buf.task = task || '';
      buf.index = idx;
      buf.total = total;
      ctx.atBottom();
      return row;
    }

    function bumpSwarmProgressLive(aid) {
      var buf = subagentBuffers.get(aid);
      if (!buf || !buf.row) return;
      buf.row.classList.add('active');
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) {
        var w = parseFloat(bar.style.width) || 0;
        bar.style.width = Math.min(92, w + 7) + '%';
      }
    }

    function markSwarmDoneLive(aid, status) {
      var buf = subagentBuffers.get(aid);
      if (!buf) return;
      buf.status = status === 'error' ? 'error' : 'done';
      if (!buf.row) return;
      buf.row.classList.remove('active');
      buf.row.classList.add(buf.status);
      var bar = buf.row.querySelector('.swarm-progress-bar');
      if (bar) bar.style.width = '100%';
      if (activeDetailAgent === aid && detailPanel) {
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

    function openDetailPanel(aid) {
      activeDetailAgent = aid;
      var buf = subagentBuffers.get(aid) || getBuffer(aid);
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
          var base = item.buf.name || item.id;
          nameCounts[base] = (nameCounts[base] || 0) + 1;
        });
        var nameSeen = {};
        var switcher = document.createElement('nav');
        switcher.className = 'si-switcher';
        switcher.setAttribute('aria-label', 'Subagents in this turn');
        bufferList.forEach(function (item) {
          var id = item.id;
          var b = item.buf;
          var base = b.name || id;
          nameSeen[base] = (nameSeen[base] || 0) + 1;
          var pill = document.createElement('button');
          pill.type = 'button';
          pill.className = 'si-pill' + (id === aid ? ' active' : '');
          var st = b.status || 'running';
          var cColor = ctx.agentColor(id);
          var cLetter = ctx.esc((b.name || id || '?').slice(0, 1).toUpperCase());
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
          renderEventInDetail(ev.kind, ev.data, aid);
        });
      }

      head.querySelector('.si-close').addEventListener('click', closeDetailPanel);
      ctx.wrap.querySelectorAll('.swarm-row').forEach(function (r) {
        r.classList.toggle('selected', r.dataset.agentId === aid);
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

    function swarmRowFor(aid) {
      if (!aid) return null;
      var rows = ctx.turn.querySelectorAll('.swarm-row[data-agent-id]');
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].dataset.agentId === aid) return rows[i];
      }
      return null;
    }

    function bumpSwarmProgressResume(aid) {
      if (!aid) return;
      var row = swarmRowFor(aid);
      if (!row) return;
      row.classList.add('active');
      var bar = row.querySelector('.swarm-progress-bar');
      if (bar) {
        var w = parseFloat(bar.style.width) || 0;
        bar.style.width = Math.min(92, w + 7) + '%';
      }
    }

    function ensureSwarmRow(aid, name, task, idx, total) {
      if (!aid) return;
      subagentSet.add(aid);
      var row = swarmRowFor(aid);
      if (row) {
        bumpSwarmProgressResume(aid);
        return row;
      }
      var card = ctx.turn.querySelector('.swarm-card');
      if (!card) {
        card = document.createElement('div');
        card.className = 'swarm-card';
        ctx.turn.appendChild(card);
      }
      row = document.createElement('div');
      row.className = 'swarm-row active';
      row.dataset.agentId = aid;
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

    function markSwarmDoneResume(aid, status) {
      if (!aid) return;
      var row = swarmRowFor(aid);
      if (!row) return;
      row.classList.remove('active');
      row.classList.add(status === 'error' ? 'error' : 'done');
      var bar = row.querySelector('.swarm-progress-bar');
      if (bar) bar.style.width = '100%';
    }

    function bumpSwarmProgress(aid) {
      if (isLive) bumpSwarmProgressLive(aid);
      else bumpSwarmProgressResume(aid);
    }

    function markSwarmDone(aid, status) {
      if (isLive) markSwarmDoneLive(aid, status);
      else markSwarmDoneResume(aid, status);
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
      if (isLive) {
        turnActive = true;
        adoptAgent(d.agent_id, d.agent);
        if (!asstEl) showPending();
      } else {
        sawTurnEvent = true;
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

    es.addEventListener('delegate', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var target = d.to || d.agent_id || '';
      var name = d.agent || target;
      var task = d.task || d.reason || '';
      var idx = d.parallel_index || 1;
      var total = d.parallel_total || 1;
      if (target) subagentSet.add(target);
      if (isLive) {
        createSwarmCard();
        var buf = getBuffer(target);
        buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
        if (!buf.row) addSwarmRow(target, name, task, idx, total);
        ctx.setStatus('amber', 'busy \u00b7 ' + (total > 1 ? total + ' agents' : name));
      } else {
        ensureSwarmRow(target, name, task, idx, total);
        ctx.setStatus('amber', ctx.busyStatusLabel());
      }
      ctx.atBottom();
    });

    es.addEventListener('subagent_start', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      var name = d.agent || aid;
      var task = d.task || '';
      var idx = d.parallel_index || 1;
      var total = d.parallel_total || 1;
      if (aid) subagentSet.add(aid);
      if (isLive) {
        var buf = getBuffer(aid);
        buf.name = name; buf.task = task; buf.index = idx; buf.total = total;
        if (!buf.row) addSwarmRow(aid, name, task, idx, total);
        buf.row.classList.add('active');
      } else {
        ensureSwarmRow(aid, name, task, idx, total);
      }
      ctx.atBottom();
    });

    es.addEventListener('subagent_done', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      markSwarmDone(d.agent_id || '', d.status || 'ok');
      ctx.atBottom();
    });

    es.addEventListener('thinking', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        if (isLive) { bufferEvent(d.agent_id, 'thinking', d); }
        bumpSwarmProgress(d.agent_id);
        return;
      }
      adoptAgent(d.agent_id, d.agent);
      clearPending();
      if (!thinkEl) { thinkEl = document.createElement('div'); thinkEl.className = 'thinking'; ctx.turn.appendChild(thinkEl); }
      thinkEl.textContent += d.content || '';
      ctx.atBottom();
    });

    es.addEventListener('tool', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      if (aid && subagentSet.has(aid) && aid !== ctx.agentId) {
        if (isLive) { bufferEvent(aid, 'tool', d); }
        bumpSwarmProgress(aid);
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
      dropEmptyAssistant();
      ctx.turn.appendChild(buildToolCard(d));
      ctx.atBottom();
    });

    es.addEventListener('tool_result', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      var aid = d.agent_id || '';
      if (aid && subagentSet.has(aid) && aid !== ctx.agentId) {
        if (isLive) { bufferEvent(aid, 'tool_result', d); }
        bumpSwarmProgress(aid);
        return;
      }
      if (!isLive) {
        resultSeen++;
        if (resultSeen <= skipResults) return;
      }
      applyToolResult(d);
    });

    es.addEventListener('todos', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        if (isLive) { bufferEvent(d.agent_id, 'todos', d); }
        bumpSwarmProgress(d.agent_id);
        return;
      }
      if (Array.isArray(d.todos) && window.Tomo && Tomo.upsertTodoPanel) {
        clearPending();
        Tomo.upsertTodoPanel(ctx.turn, d.todos);
        if (!asstEl && !pendingEl) showPending();
        ctx.atBottom();
      }
    });

    es.addEventListener('delta', function (e) {
      bumpActivity();
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        if (isLive) { bufferEvent(d.agent_id, 'delta', d); }
        bumpSwarmProgress(d.agent_id);
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

    es.addEventListener('done', function (e) {
      bumpActivity();
      sawDone = true;
      if (isLive) {
        armIdle(POST_DONE_MS);
      }
      var d = JSON.parse(e.data || '{}');
      if (!isLive) sawTurnEvent = true;
      if (isSubagentEvent(d)) {
        if (isLive) return;
        bumpSwarmProgress(d.agent_id);
        return;
      }
      adoptAgent(d.agent_id, d.agent);
      if (thinkEl) { thinkEl.remove(); thinkEl = null; }
      var content = (d.content != null ? String(d.content) : '').trim();
      if (content.indexOf('[Swarm]') === 0) content = '';
      if (content) {
        ensureAssistantBubble();
        raw = content;
        ctx.setMarkdown(asstBody, raw);
        asstEl.classList.remove('streaming');
      } else {
        clearPending();
        dropEmptyAssistant();
      }
      ctx.atBottom();
      ctx.setStatus('amber', ctx.busyStatusLabel());
    });

    es.addEventListener('turn.end', function () {
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
          if (isLive) { bufferEvent(errAgentId, 'error', { message: msg }); markSwarmDone(errAgentId, 'error'); }
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
        errorBubble('<span style="color:var(--danger)">' + ctx.esc(msg) + '</span>');
        endTurn();
        return;
      }
      if (isLive) {
        if (turnActive || sawDone) { endTurn(); return; }
        errorBubble('<span style="color:var(--danger)">Stream interrupted</span>');
        endTurn();
      } else {
        if (sawTurnEvent) return;
        endIdleResume();
      }
    });

    es.addEventListener('heartbeat', function () {
      if (sawDone) return;
      if (!isLive && !sawTurnEvent) {
        endIdleResume();
        return;
      }
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
      hardTimer = setTimeout(function () {
        if (closed) return;
        endTurn();
      }, HARD_MS);
      armIdle(IDLE_MS);
      setTimeout(function () {
        if (!closed && !sawTurnEvent && subagentSet.size === 0) endIdleResume();
      }, 1000);
    } else {
      hardTimer = setTimeout(function () {
        if (closed) return;
        console.warn('[tomo] turn hard timeout');
        errorBubble('<span style="color:var(--danger)">Turn timed out. You can send again.</span>');
        endTurn();
      }, HARD_MS);
      armIdle(IDLE_MS);
    }

    return { end: endTurn };
  }

  window.TomoTurnStream = { attach: attach };
})();

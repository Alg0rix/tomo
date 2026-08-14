/* chat.js — streaming chat client (agent detail + swarm sessions page).
 * Event protocol: state · turn.start · session · thinking · tool · tool_result · delta · done · delegate · error · heartbeat
 */
(function () {
  "use strict";

  function esc(s) { return Tomo.escapeHtml(s); }

  /**
   * POST-based SSE client (EventSource cannot set method/body).
   * Same addEventListener/close surface as EventSource for chat handlers.
   */
  function postEventSource(url, body) {
    var listeners = {};
    var closed = false;
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;

    function emit(type, data) {
      var list = listeners[type] || [];
      for (var i = 0; i < list.length; i++) {
        try {
          list[i]({ data: data == null ? "" : String(data) });
        } catch (_) {}
      }
    }

    var api = {
      addEventListener: function (type, fn) {
        if (!listeners[type]) listeners[type] = [];
        listeners[type].push(fn);
      },
      close: function () {
        closed = true;
        if (controller) controller.abort();
      },
    };

    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body || {}),
      signal: controller ? controller.signal : undefined,
    })
      .then(function (res) {
        if (closed) return;
        if (!res.ok) {
          emit("error", JSON.stringify({ message: "HTTP " + res.status }));
          emit("turn.end", "{}");
          return null;
        }
        if (!res.body || !res.body.getReader) {
          return res.text().then(function (text) {
            if (closed) return;
            parseSseBuffer(text, emit);
            emit("turn.end", "{}");
          });
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";
        function pump() {
          return reader.read().then(function (result) {
            if (closed) return;
            if (result.done) {
              if (buf.trim()) parseSseBuffer(buf, emit);
              // Body closed without an explicit client close. If turn.end already
              // ran, listeners no-op; otherwise reconnect can rejoin the turn.
              emit("stream_closed", "{}");
              return;
            }
            buf += decoder.decode(result.value, { stream: true });
            var parts = buf.split("\n\n");
            buf = parts.pop() || "";
            for (var i = 0; i < parts.length; i++) {
              dispatchSseBlock(parts[i], emit);
            }
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        if (closed) return;
        var name = err && err.name;
        if (name === "AbortError") return;
        emit("error", JSON.stringify({ message: String(err && err.message ? err.message : err) }));
        // Do not force turn.end — mid-turn errors should reconnect to listen.
        emit("stream_closed", "{}");
      });

    return api;
  }

  function dispatchSseBlock(block, emit) {
    var event = "message";
    var dataLines = [];
    var lines = String(block || "").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.indexOf("event:") === 0) event = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (dataLines.length) emit(event, dataLines.join("\n"));
  }

  function parseSseBuffer(text, emit) {
    var parts = String(text || "").split("\n\n");
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].trim()) dispatchSseBlock(parts[i], emit);
    }
  }

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
    var raw = text == null ? '' : String(text);
    el.dataset.raw = raw;
    el.dataset.md = '0';
    var partial = !!(el.closest && el.closest('.streaming, .msg.streaming'));
    if (window.TomoMarkdown && TomoMarkdown.renderInto) {
      TomoMarkdown.renderInto(el, raw, { partial: partial });
      return;
    }
    el.textContent = raw;
    renderMarkdown(el);
  }

  function agentColor(id) {
    return (window.Tomo && Tomo.avatarColor) ? Tomo.avatarColor(id) : 'var(--accent)';
  }

  var ICON_COPY =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var ICON_EDIT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
  var ICON_REGEN =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 0-15.5-6.36L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 15.5 6.36L21 16"/><path d="M21 21v-5h-5"/></svg>';
  var ICON_CHECK =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

  function msgActionsHtml(role) {
    if (role === 'user') {
      return '<div class="msg-actions" data-role="user">' +
        '<button type="button" class="msg-act" data-act="copy" title="Copy" aria-label="Copy">' + ICON_COPY + '</button>' +
        '<button type="button" class="msg-act" data-act="edit" title="Edit message" aria-label="Edit message">' + ICON_EDIT + '</button>' +
        '</div>';
    }
    return '<div class="msg-actions" data-role="assistant">' +
      '<button type="button" class="msg-act" data-act="copy" title="Copy" aria-label="Copy">' + ICON_COPY + '</button>' +
      '<button type="button" class="msg-act" data-act="regen" title="Regenerate" aria-label="Regenerate">' + ICON_REGEN + '</button>' +
      '</div>';
  }

  function bubbleHtml(role, agentName, agentId) {
    if (role === 'user') {
      return '<div class="msg user"><div class="bubble"><div class="bubble-body prose chat-prose"></div>' + msgActionsHtml('user') + '</div></div>';
    }
    const av = esc((agentName || 'A').slice(0, 1).toUpperCase());
    const who = esc(agentName || 'Agent');
    const style = agentId ? ' style="background:' + agentColor(agentId) + '"' : '';
    return '<div class="msg assistant"><div class="av"' + style + '>' + av + '</div><div class="bubble"><div class="who">' + who + '</div><div class="bubble-body prose chat-prose"></div>' + msgActionsHtml('assistant') + '</div></div>';
  }

  function msgPlainText(msg) {
    var body = msg && msg.querySelector('.bubble-body');
    if (!body) return '';
    if (body.dataset.raw) return body.dataset.raw;
    var clone = body.cloneNode(true);
    clone.querySelectorAll('.bubble-attachments, .msg-actions').forEach(function (n) { n.remove(); });
    return (clone.innerText || clone.textContent || '').replace(/\s+\n/g, '\n').trim();
  }

  function ensureMsgActions(root) {
    if (!root) return;
    root.querySelectorAll('.msg.user, .msg.assistant').forEach(function (msg) {
      if (msg.querySelector('.msg-actions')) return;
      if (msg.classList.contains('streaming')) return;
      var bubble = msg.querySelector('.bubble');
      if (!bubble) return;
      var role = msg.classList.contains('user') ? 'user' : 'assistant';
      bubble.insertAdjacentHTML('beforeend', msgActionsHtml(role));
    });
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
    const stopBtn = wrap.querySelector('.chat-stop');
    const clearBtn = wrap.querySelector('.chat-clear');
    const statusEl = wrap.querySelector('.chat-status');
    const mentionMenu = wrap.querySelector('.mention-menu');
    const slashMenu = wrap.querySelector('.slash-menu');
    const mcpResourceMenu = wrap.querySelector('.mcp-resource-menu');
    const mcpResourcesBtn = wrap.querySelector('.chat-mcp-resources-btn');
    const attachBtn = wrap.querySelector('.attach-btn');
    const attachInput = wrap.querySelector('.attachment-input');
    const attachPreview = wrap.querySelector('.attachment-preview');
    const composerEl = wrap.querySelector('.composer');
    const moreWrap = wrap.querySelector('.composer-mobile-more');
    const moreBtn = moreWrap && moreWrap.querySelector('.composer-mobile-more-btn');
    const morePanel = moreWrap && moreWrap.querySelector('.composer-mobile-more-panel');
    const modeBtn = wrap.querySelector('.composer-mode') || document.getElementById('composerModeBtn');
    const reasoningEl = wrap.querySelector('.composer-reasoning');
    const reasoningTrigger = reasoningEl && reasoningEl.querySelector('.composer-reasoning-trigger');
    const reasoningPopover = reasoningEl && reasoningEl.querySelector('.composer-reasoning-popover');
    const reasoningFlyout = reasoningEl && reasoningEl.querySelector('.composer-reasoning-flyout');
    const reasoningModel = reasoningEl && reasoningEl.querySelector('.composer-reasoning-model');
    const reasoningTriggerEffort = reasoningEl && reasoningEl.querySelector('.composer-reasoning-trigger-effort');
    const reasoningRows = reasoningEl ? reasoningEl.querySelectorAll('.composer-reasoning-row') : [];
    const reasoningReset = reasoningEl && reasoningEl.querySelector('.composer-reasoning-reset');
    const defaultAgentName = wrap.dataset.agentName || (wrap.querySelector('.chat-agent-name') || {}).textContent || 'Agent';

    /** @type {{id: string, name: string, size: number}[]} */
    let uploadedAttachments = [];
    let uploading = false;
    const MAX_ATTACH_BYTES = 20 * 1024 * 1024;

    let mentionOpen = false;
    let mentionIndex = 0;
    let mentionMatches = [];
    let mentionRange = null; // {start, end} of @query in input

    let slashOpen = false;
    let slashIndex = 0;
    let slashMatches = [];
    /** @type {{id: string, name: string, description: string}[]} */
    let skillsCache = null;
    let skillsLoading = false;
    /** @type {{kind: string, serverId: string, itemId: string, id: string, name: string, description: string, args: object[]}[]} */
    let mcpPromptsCache = null;
    let mcpPromptsLoading = false;
    let mcpResourcesCache = null;
    let mcpResourcesLoading = false;

    // Cycle: Manual → Smart → Auto(off) → Manual
    var MODE_CYCLE = ['manual', 'smart', 'off'];
    var MODE_LABEL = { manual: 'Manual', smart: 'Smart', off: 'Auto' };
    var reasoningState = null;

    function currentSessionId() { return wrap.dataset.sessionId || ''; }

    function closeMoreMenu() {
      if (!moreWrap || !moreBtn || !morePanel) return;
      moreBtn.setAttribute('aria-expanded', 'false');
      morePanel.setAttribute('aria-hidden', 'true');
      moreWrap.classList.remove('is-open');
    }

    function openMoreMenu() {
      if (!moreWrap || !moreBtn || !morePanel) return;
      moreBtn.setAttribute('aria-expanded', 'true');
      morePanel.setAttribute('aria-hidden', 'false');
      moreWrap.classList.add('is-open');
    }

    function toggleMoreMenu() {
      if (!moreBtn || !morePanel) return;
      if (moreBtn.getAttribute('aria-expanded') === 'true') closeMoreMenu();
      else openMoreMenu();
    }

    function onMoreDocumentPointerDown(event) {
      if (moreWrap && !moreWrap.contains(event.target)) closeMoreMenu();
    }

    function onMoreEscape(event) {
      if (event.key === 'Escape') closeMoreMenu();
    }

    if (moreBtn && morePanel && moreWrap) {
      moreBtn.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        toggleMoreMenu();
      });
      morePanel.addEventListener('click', function (event) {
        var action = event.target.closest('button');
        if (!action || action === moreBtn) return;
        window.setTimeout(closeMoreMenu, 0);
      });
      document.addEventListener('pointerdown', onMoreDocumentPointerDown);
      document.addEventListener('keydown', onMoreEscape);
    }

    function closeReasoningMenus() {
      if (reasoningPopover) reasoningPopover.classList.add('hidden');
      if (reasoningFlyout) reasoningFlyout.classList.add('hidden');
      if (reasoningTrigger) reasoningTrigger.setAttribute('aria-expanded', 'false');
    }

    function paintReasoningEffort(payload) {
      reasoningState = payload || null;
      if (!reasoningEl) return;
      var efforts = payload && Array.isArray(payload.reasoning_efforts)
        ? payload.reasoning_efforts : [];
      if (!payload || !currentSessionId() || !efforts.length) {
        reasoningEl.classList.add('hidden');
        closeReasoningMenus();
        return;
      }
      var model = String(payload.model || payload.profile_name || 'Model');
      var active = String(payload.reasoning_effort || payload.default_reasoning_effort || efforts[efforts.length - 1] || '');
      reasoningEl.classList.remove('hidden');
      if (reasoningModel) reasoningModel.textContent = model;
      if (reasoningTriggerEffort) reasoningTriggerEffort.textContent = active;
      var rowValues = reasoningEl.querySelectorAll('.composer-reasoning-row-value');
      if (rowValues[0]) rowValues[0].textContent = model;
      if (rowValues[1]) rowValues[1].textContent = active;
      if (reasoningFlyout) {
        reasoningFlyout.innerHTML = efforts.map(function (effort) {
          var value = String(effort);
          var selected = value === active;
          return '<button type="button" class="composer-reasoning-option' + (selected ? ' is-active' : '') + '" data-effort="' + esc(value) + '" role="menuitemradio" aria-checked="' + (selected ? 'true' : 'false') + '">' +
            '<span>' + esc(value) + '</span><span class="check" aria-hidden="true">' + (selected ? '✓' : '') + '</span></button>';
        }).join('');
        reasoningFlyout.querySelectorAll('.composer-reasoning-option').forEach(function (option) {
          option.addEventListener('click', function (event) {
            event.preventDefault();
            persistReasoningEffort(option.getAttribute('data-effort'));
          });
        });
      }
    }

    async function persistReasoningEffort(value) {
      var sid = currentSessionId();
      if (!sid) return;
      var before = reasoningState;
      try {
        var data = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/reasoning-effort',
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reasoning_effort: value == null ? '' : String(value) }),
          }
        );
        paintReasoningEffort(data);
        closeReasoningMenus();
      } catch (e) {
        paintReasoningEffort(before);
        if (window.Tomo && Tomo.toast) {
          Tomo.toast((e && e.message) || 'Could not change reasoning effort', 'err');
        }
      }
    }

    async function refreshReasoningEffort() {
      var sid = currentSessionId();
      if (!sid || !reasoningEl) {
        paintReasoningEffort(null);
        return;
      }
      try {
        var data = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/reasoning-effort'
        );
        paintReasoningEffort(data);
      } catch (e) {
        paintReasoningEffort(null);
      }
    }

    function toggleReasoningPopover() {
      if (!reasoningPopover || reasoningEl.classList.contains('hidden')) return;
      var open = reasoningPopover.classList.toggle('hidden') === false;
      if (!open && reasoningFlyout) reasoningFlyout.classList.add('hidden');
      if (reasoningTrigger) reasoningTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    if (reasoningTrigger) {
      reasoningTrigger.addEventListener('click', function (event) {
        event.preventDefault();
        toggleReasoningPopover();
      });
    }
    if (reasoningRows.length) {
      reasoningRows.forEach(function (row) {
        row.addEventListener('click', function (event) {
          event.preventDefault();
          if (row.getAttribute('data-reasoning-row') !== 'effort' || !reasoningFlyout) return;
          reasoningFlyout.classList.toggle('hidden');
        });
      });
    }
    if (reasoningReset) {
      reasoningReset.addEventListener('click', function (event) {
        event.preventDefault();
        persistReasoningEffort('');
      });
    }
    function onReasoningDocumentClick(event) {
      if (reasoningEl && !reasoningEl.contains(event.target)) closeReasoningMenus();
    }
    document.addEventListener('click', onReasoningDocumentClick);
    function onReasoningEscape(event) {
      if (event.key === 'Escape') closeReasoningMenus();
    }
    document.addEventListener('keydown', onReasoningEscape);

    function paintApprovalMode(payload) {
      if (!modeBtn) return;
      var mode = (payload && payload.mode) || 'smart';
      var label = (payload && payload.label) || MODE_LABEL[mode] || 'Smart';
      modeBtn.dataset.mode = mode;
      var key = modeBtn.querySelector('.composer-mode-key');
      if (key) key.textContent = label;
      else modeBtn.innerHTML =
        '<span class="composer-mode-key">' + esc(label) + '</span>' +
        '<span class="composer-mode-suffix"> Mode</span>';
      modeBtn.title =
        'Permission: ' + label + ' — click to cycle (/manual /smart /auto)';
    }

    async function refreshApprovalMode() {
      var sid = currentSessionId();
      if (!sid || !modeBtn) return;
      try {
        var data = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/approval-mode'
        );
        if (data) paintApprovalMode(data);
      } catch (e) { /* ignore */ }
    }

    async function cycleApprovalMode() {
      var sid = currentSessionId();
      if (!sid) {
        if (window.Tomo && Tomo.toast) Tomo.toast('Open a chat first', 'err');
        return;
      }
      var cur = modeBtn ? modeBtn.dataset.mode || 'smart' : 'smart';
      var idx = MODE_CYCLE.indexOf(cur);
      var next = MODE_CYCLE[(idx < 0 ? 0 : idx + 1) % MODE_CYCLE.length];
      try {
        var data = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/approval-mode',
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: next }),
          }
        );
        if (data) {
          paintApprovalMode(data);
          if (data.cleared_pending && window.Tomo && Tomo.toast) {
            Tomo.toast(
              'Auto — cleared ' + data.cleared_pending + ' pending approval(s)',
              'ok'
            );
          }
        }
      } catch (e) {
        if (window.Tomo && Tomo.toast) {
          Tomo.toast('Could not change permission mode', 'err');
        }
      }
    }

    if (modeBtn) {
      modeBtn.addEventListener('click', function (e) {
        e.preventDefault();
        cycleApprovalMode();
      });
    }
    refreshApprovalMode();
    refreshReasoningEffort();

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

    function hideSlash() {
      slashOpen = false;
      slashMatches = [];
      if (slashMenu) {
        slashMenu.classList.add('hidden');
        slashMenu.innerHTML = '';
      }
    }

    function hideMcpResources() {
      if (mcpResourceMenu) {
        mcpResourceMenu.classList.add('hidden');
        mcpResourceMenu.innerHTML = '';
      }
    }

    function hidePopups() {
      hideMentions();
      hideSlash();
      hideMcpResources();
    }

    function ensureSkills(cb, force) {
      if (skillsCache && !force) {
        if (cb) cb(skillsCache);
        return;
      }
      if (skillsLoading) return;
      skillsLoading = true;
      fetch('/api/skills', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { skills: [] }; })
        .then(function (data) {
          skillsCache = (data.skills || []).filter(function (s) {
            return s && s.enabled !== false && (s.id || s.name);
          });
          skillsLoading = false;
          if (cb) cb(skillsCache);
        })
        .catch(function () {
          skillsCache = skillsCache || [];
          skillsLoading = false;
          if (cb) cb(skillsCache);
        });
    }

    // MCP prompts entered into the slash menu alongside skills. Only enabled
    // items on connected, enabled servers are offered.
    function ensureMcpPrompts(cb, force) {
      if (mcpPromptsCache && !force) {
        if (cb) cb(mcpPromptsCache);
        return;
      }
      if (mcpPromptsLoading) return;
      mcpPromptsLoading = true;
      fetch('/api/mcp-servers', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { servers: [] }; })
        .then(function (data) {
          var servers = (data.servers || []).filter(function (s) {
            return s.enabled && s.status === 'connected';
          });
          return Promise.all(servers.map(function (s) {
            return fetch('/api/mcp-servers/' + encodeURIComponent(s.id) + '/prompts', { credentials: 'same-origin' })
              .then(function (r) { return r.ok ? r.json() : { prompts: [] }; })
              .then(function (d) {
                return (d.prompts || []).filter(function (i) { return i.enabled; }).map(function (i) {
                  return {
                    kind: 'mcp_prompt',
                    serverId: s.id,
                    itemId: i.id,
                    id: s.id + '/' + i.name,
                    name: i.name,
                    description: i.description || i.title || i.name,
                    args: (i.schema && i.schema.arguments) || [],
                  };
                });
              })
              .catch(function () { return []; });
          }));
        })
        .then(function (lists) {
          mcpPromptsCache = [].concat.apply([], lists);
          mcpPromptsLoading = false;
          if (cb) cb(mcpPromptsCache);
        })
        .catch(function () {
          mcpPromptsCache = mcpPromptsCache || [];
          mcpPromptsLoading = false;
          if (cb) cb(mcpPromptsCache);
        });
    }

    // MCP prompts take arguments and are fetched, not slash-expanded —
    // selecting one must not insert a slash token or auto-send (keeps
    // services/chat.py skill-expansion semantics untouched).
    async function selectMcpPrompt(entry) {
      hideSlash();
      var argValues = {};
      var args = entry.args || [];
      for (var i = 0; i < args.length; i++) {
        var a = args[i];
        if (!a.required) continue;
        var label = a.description ? (a.name + ' — ' + a.description) : a.name;
        var val = window.prompt(label, '');
        if (val === null) return; // cancelled
        argValues[a.name] = val;
      }
      try {
        var res = await fetch('/api/mcp-servers/' + encodeURIComponent(entry.serverId) + '/prompts/get', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: entry.name, arguments: argValues }),
        });
        var d = res.ok ? await res.json() : null;
        if (!d) {
          Tomo.toast('Could not load MCP prompt', 'err');
          return;
        }
        var text = (d.messages || []).map(function (m) { return m.text; }).join('\n\n');
        input.value = text;
        resize();
        refreshSendBtn();
        input.focus();
      } catch (e) {
        Tomo.toast('Could not load MCP prompt', 'err');
      }
    }

    // MCP resources: "Resources" composer action inserts a marked context
    // block (never raw base64) into the message rather than sending it.
    function ensureMcpResources(cb, force) {
      if (mcpResourcesCache && !force) {
        if (cb) cb(mcpResourcesCache);
        return;
      }
      if (mcpResourcesLoading) return;
      mcpResourcesLoading = true;
      fetch('/api/mcp-servers', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { servers: [] }; })
        .then(function (data) {
          var servers = (data.servers || []).filter(function (s) {
            return s.enabled && s.status === 'connected';
          });
          return Promise.all(servers.map(function (s) {
            return fetch('/api/mcp-servers/' + encodeURIComponent(s.id) + '/resources', { credentials: 'same-origin' })
              .then(function (r) { return r.ok ? r.json() : { resources: [] }; })
              .then(function (d) {
                return (d.resources || []).filter(function (i) {
                  return i.enabled && i.kind === 'resource';
                }).map(function (i) {
                  return {
                    serverId: s.id,
                    serverName: s.name,
                    uri: i.uri,
                    name: i.title || i.name,
                    description: i.description,
                  };
                });
              })
              .catch(function () { return []; });
          }));
        })
        .then(function (lists) {
          mcpResourcesCache = [].concat.apply([], lists);
          mcpResourcesLoading = false;
          if (cb) cb(mcpResourcesCache);
        })
        .catch(function () {
          mcpResourcesCache = mcpResourcesCache || [];
          mcpResourcesLoading = false;
          if (cb) cb(mcpResourcesCache);
        });
    }

    function renderMcpResourceMenu(items) {
      if (!mcpResourceMenu) return;
      if (!items.length) {
        mcpResourceMenu.innerHTML = '<div class="slash-item" role="option">' +
          '<span class="slash-name">No MCP resources</span>' +
          '<span class="slash-desc">Connect a server in System → MCP</span></div>';
        mcpResourceMenu.classList.remove('hidden');
        return;
      }
      mcpResourceMenu.innerHTML = items.map(function (it, i) {
        return '<button type="button" class="slash-item" data-idx="' + i + '" role="option">' +
          '<span class="slash-name">' + esc(it.name) + '</span>' +
          '<span class="slash-desc">' + esc(it.serverName) + ' · ' + esc(it.description || it.uri) + '</span></button>';
      }).join('');
      mcpResourceMenu.classList.remove('hidden');
      mcpResourceMenu.querySelectorAll('.slash-item[data-idx]').forEach(function (btn) {
        btn.addEventListener('mousedown', function (e) {
          e.preventDefault();
          insertMcpResource(items[parseInt(btn.dataset.idx, 10) || 0]);
        });
      });
    }

    async function insertMcpResource(item) {
      hideMcpResources();
      try {
        var res = await fetch('/api/mcp-servers/' + encodeURIComponent(item.serverId) + '/resources/read', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ uri: item.uri }),
        });
        var d = res.ok ? await res.json() : null;
        if (!d) {
          Tomo.toast('Could not read MCP resource', 'err');
          return;
        }
        var blocks = (d.contents || []).map(function (c) {
          return c.kind === 'text'
            ? c.text
            : ('[' + (c.mime_type || 'binary') + ', ' + (c.size_base64_chars || 0) + ' base64 chars — not inserted]');
        });
        var marked = '--- MCP resource: ' + item.name + ' (' + item.uri + ') ---\n' +
          blocks.join('\n\n') + '\n--- end resource ---';
        input.value = (input.value ? input.value.replace(/\s+$/, '') + '\n\n' : '') + marked + '\n';
        resize();
        refreshSendBtn();
        input.focus();
      } catch (e) {
        Tomo.toast('Could not read MCP resource', 'err');
      }
    }

    if (mcpResourcesBtn) {
      mcpResourcesBtn.addEventListener('click', function (e) {
        e.preventDefault();
        if (mcpResourceMenu && !mcpResourceMenu.classList.contains('hidden')) {
          hideMcpResources();
          return;
        }
        hideMentions();
        hideSlash();
        closeMoreMenu();
        ensureMcpResources(function (items) { renderMcpResourceMenu(items); }, false);
      });
    }

    const APPROVAL_SLASH = [
      { id: 'auto', name: 'auto', description: 'Toggle AUTO — run tools without approval prompts' },
      { id: 'smart', name: 'smart', description: 'Smart approvals — aux LLM assesses risky tools' },
      { id: 'manual', name: 'manual', description: 'Manual approvals — always ask for risky tools' },
    ];

    function filterSkills(query) {
      var q = (query || '').toLowerCase().replace(/^\//, '');
      var builtins = APPROVAL_SLASH.filter(function (s) {
        if (!q) return true;
        return s.id.indexOf(q) === 0 || s.name.indexOf(q) === 0;
      });
      var all = (skillsCache || []).concat(mcpPromptsCache || []);
      if (!q) {
        return builtins.concat(all.slice(0, Math.max(0, 14 - builtins.length)));
      }
      var ranked = all.map(function (s, index) {
        var id = String(s.id || '').toLowerCase();
        var name = String(s.name || '').toLowerCase();
        var desc = String(s.description || '').toLowerCase();
        var score = 0;
        if (id === q || name === q) score = 3;
        else if (id.indexOf(q) === 0 || name.indexOf(q) === 0) score = 2;
        else if (id.indexOf(q) >= 0 || name.indexOf(q) >= 0 || desc.indexOf(q) >= 0) score = 1;
        return { s: s, index: index, score: score };
      }).filter(function (x) { return x.score > 0; })
        .sort(function (a, b) {
          if (a.score !== b.score) return b.score - a.score;
          return a.index - b.index;
        })
        .slice(0, Math.max(0, 14 - builtins.length))
        .map(function (x) { return x.s; });
      return builtins.concat(ranked);
    }

    function renderSlashMenu() {
      if (!slashMenu) return;
      if (!slashMatches.length) {
        hideSlash();
        return;
      }
      slashMenu.innerHTML = slashMatches.map(function (s, i) {
        var active = i === slashIndex ? ' active' : '';
        var isMcpPrompt = s.kind === 'mcp_prompt';
        var sid = s.id || s.name || '';
        var label = isMcpPrompt ? ('/' + (s.name || sid)) : ('/' + sid);
        var desc = (isMcpPrompt ? 'MCP · ' : '') + (s.description || s.name || '');
        return '<button type="button" class="slash-item' + active + '" data-idx="' + i + '" role="option">' +
          '<span class="slash-name">' + esc(label) + '</span>' +
          '<span class="slash-desc">' + esc(desc) + '</span></button>';
      }).join('');
      slashMenu.classList.remove('hidden');
      slashOpen = true;
      slashMenu.querySelectorAll('.slash-item').forEach(function (btn) {
        btn.addEventListener('mousedown', function (e) {
          e.preventDefault();
          var idx = parseInt(btn.dataset.idx, 10) || 0;
          var picked = slashMatches[idx];
          if (picked && picked.kind === 'mcp_prompt') {
            selectMcpPrompt(picked);
            return;
          }
          insertSlash(idx);
        });
      });
    }

    function updateSlash() {
      if (!slashMenu || !input) return;
      var val = input.value;
      // Only when the whole input is a single /token (no space yet).
      if (!(val.startsWith('/') && val.indexOf(' ') < 0)) {
        hideSlash();
        return;
      }
      hideMentions();
      // Re-fetch when opening so mid-session installs/servers show up.
      var forceSkills = !skillsCache || (slashMenu && slashMenu.classList.contains('hidden'));
      var forcePrompts = !mcpPromptsCache || (slashMenu && slashMenu.classList.contains('hidden'));
      var pending = 2;
      function maybeRender() {
        pending -= 1;
        if (pending > 0) return;
        var cur = input.value;
        if (!(cur.startsWith('/') && cur.indexOf(' ') < 0)) {
          hideSlash();
          return;
        }
        slashMatches = filterSkills(cur);
        slashIndex = 0;
        renderSlashMenu();
      }
      ensureSkills(maybeRender, forceSkills);
      ensureMcpPrompts(maybeRender, forcePrompts);
    }

    function insertSlash(idx) {
      if (!input || !slashMatches[idx]) return;
      var skill = slashMatches[idx];
      var sid = skill.id || skill.name || '';
      input.value = '/' + sid + ' ';
      var pos = input.value.length;
      input.setSelectionRange(pos, pos);
      hideSlash();
      input.focus();
      refreshSendBtn();
      resize();
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
      // Slash menu owns the popup when input is a lone /token.
      if (input && input.value.startsWith('/') && input.value.indexOf(' ') < 0) {
        hideMentions();
        return;
      }
      const hit = detectMention();
      if (!hit) {
        hideMentions();
        return;
      }
      hideSlash();
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

    // Prefetch skills so first `/` feels instant.
    ensureSkills();

    // Session chat may be a client-side draft (pendingAgents, no sessionId yet).
    if (!scroll || !input || !sendBtn || (!agentId && !currentSessionId() && !pendingAgentIds().length)) return;

    let sending = false, es = null;
    /** @type {{text: string, el: Element|null, attachmentIds: string[]}[]} */
    let messageQueue = [];
    const MAX_QUEUE = 20;

    function atBottom() {
      if (Tomo.nudgeScrollBottom) {
        // If a stick is active, go() through the RO path without restarting the stick.
        // Otherwise fall through to a one-shot instant scroll (don't start a stick on
        // every token).
        if (typeof scroll._tomoStickGo === 'function') {
          scroll._tomoStickGo();
          return;
        }
      }
      if (window.Tomo && Tomo.scrollToBottomInstant) {
        Tomo.scrollToBottomInstant(scroll);
      } else {
        var prev = scroll.style.scrollBehavior;
        scroll.style.scrollBehavior = 'auto';
        scroll.scrollTop = scroll.scrollHeight;
        scroll.style.scrollBehavior = prev;
      }
    }
    function setStatus(badge, label) {
      if (!statusEl) return;
      // Map legacy badge tones → composer pill modifiers
      var tone = '';
      if (badge === 'amber' || badge === 'warn') tone = ' warn';
      else if (badge === 'err' || badge === 'danger') tone = ' err';
      else if (badge && badge !== 'ok') tone = ' ' + badge;
      statusEl.className = 'composer-status chat-status' + tone;
      var pretty = String(label || '').replace(/^./, function (c) {
        return c.toUpperCase();
      });
      statusEl.innerHTML =
        '<span class="composer-status-dot" aria-hidden="true"></span>' + esc(pretty);
    }

    function syncGeneratingUi() {
      // Single action slot: .is-generating toggles Send ↔ Stop in CSS
      // (do not use [hidden] — display:grid overrides it).
      if (composerEl) {
        if (sending) composerEl.classList.add('is-generating');
        else composerEl.classList.remove('is-generating');
      }
      if (stopBtn) stopBtn.disabled = !sending;
    }

    function refreshSendBtn() {
      // While a turn is running, Enter still enqueues; primary control shows Stop.
      sendBtn.disabled = uploading || (!input.value.trim() && !uploadedAttachments.length);
      syncGeneratingUi();
    }

    function formatSize(bytes) {
      if (bytes < 1024) return bytes + 'B';
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
      return (bytes / (1024 * 1024)).toFixed(1) + 'MB';
    }

    function attachmentChipsHtml(list, withRemove) {
      if (!list || !list.length) return '';
      return '<div class="bubble-attachments">' + list.map(function (att, idx) {
        return '<span class="attachment-chip" data-idx="' + idx + '">' +
          '<span class="name">' + esc(att.name || att.original_name || 'file') + '</span>' +
          (att.size != null ? '<span class="size">' + formatSize(att.size) + '</span>' : '') +
          (withRemove ? '<button type="button" class="remove" aria-label="Remove attachment" title="Remove">×</button>' : '') +
          '</span>';
      }).join('') + '</div>';
    }

    function renderAttachmentPreview() {
      if (!attachPreview) return;
      attachPreview.innerHTML = '';
      if (!uploadedAttachments.length) {
        attachPreview.classList.add('hidden');
        return;
      }
      attachPreview.classList.remove('hidden');
      attachPreview.innerHTML = attachmentChipsHtml(uploadedAttachments, true);
      Array.prototype.forEach.call(attachPreview.querySelectorAll('.attachment-chip .remove'), function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          var chip = btn.closest('.attachment-chip');
          var idx = chip ? parseInt(chip.getAttribute('data-idx'), 10) : -1;
          if (idx < 0) return;
          var removed = uploadedAttachments.splice(idx, 1)[0];
          renderAttachmentPreview();
          refreshSendBtn();
          if (removed && removed.id) {
            fetch('/api/attachments/' + encodeURIComponent(removed.id), {
              method: 'DELETE',
              credentials: 'same-origin',
            }).catch(function () { /* best-effort */ });
          }
        });
      });
    }

    async function resolveUploadSessionId() {
      var sid = currentSessionId();
      if (sid) return sid;
      try {
        sid = await ensureSession();
      } catch (e) {
        sid = '';
      }
      return sid || currentSessionId() || '';
    }

    async function uploadFiles(files) {
      if (!files.length) return;
      const sid = await resolveUploadSessionId();
      if (!sid) {
        Tomo.toast('Open or start a chat before uploading files.', 'err');
        return;
      }
      uploading = true;
      refreshSendBtn();
      setStatus('amber', 'uploading…');
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (file.size > MAX_ATTACH_BYTES) {
          Tomo.toast(file.name + ' is too large (max 20MB)', 'err');
          continue;
        }
        if (file.size === 0) {
          Tomo.toast(file.name + ' is empty', 'err');
          continue;
        }
        const form = new FormData();
        form.append('file', file);
        form.append('name', file.name);
        try {
          const resp = await fetch('/api/sessions/' + encodeURIComponent(sid) + '/attachments', {
            method: 'POST',
            body: form,
            credentials: 'same-origin',
          });
          if (!resp.ok) {
            const err = await resp.json().catch(function () { return {}; });
            var detail = err.detail;
            if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg || d; }).join('; ');
            Tomo.toast('Upload failed: ' + (detail || resp.statusText), 'err');
            continue;
          }
          const att = await resp.json();
          uploadedAttachments.push({ id: att.id, name: att.original_name || att.filename, size: att.size_bytes || 0 });
        } catch (e) {
          Tomo.toast('Upload error: ' + (e && e.message ? e.message : String(e)), 'err');
        }
      }
      uploading = false;
      renderAttachmentPreview();
      refreshSendBtn();
      syncBusyStatus();
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
    if (Tomo.stickScrollBottom) Tomo.stickScrollBottom(scroll, { holdMs: 15000, times: [50, 200, 500, 1000, 2000, 4000] });
    else atBottom();

    function resize() {
      if (!input) return;
      input.style.height = 'auto';
      var compact = window.matchMedia && window.matchMedia('(max-width: 760px)').matches;
      var minHeight = compact ? 56 : 80;
      var maxHeight = compact ? 160 : 200;
      var next = Math.max(minHeight, Math.min(input.scrollHeight, maxHeight));
      input.style.height = next + 'px';
    }

    function nextQueryIndex() {
      var max = -1;
      scroll.querySelectorAll('.turn[data-query-index]').forEach(function (turnEl) {
        var value = parseInt(turnEl.dataset.queryIndex, 10);
        if (!isNaN(value)) max = Math.max(max, value);
      });
      return max + 1;
    }

    function notifyUserTurnRemoved(turnEl) {
      var queryId = turnEl && turnEl.dataset ? turnEl.dataset.queryId : '';
      if (!queryId) return;
      wrap.dispatchEvent(new CustomEvent('tomo:user-turn-removed', {
        detail: { queryId: queryId },
      }));
    }

    function appendUserBubble(value, queued, attachments, opts) {
      opts = opts || {};
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const u = document.createElement('div');
      u.innerHTML = bubbleHtml('user', defaultAgentName);
      const bubble = u.firstElementChild;
      const body = bubble.querySelector('.bubble-body');
      body.dataset.raw = value || '';
      body.innerHTML = highlightMentions(value || '');
      if (attachments && attachments.length) {
        body.insertAdjacentHTML('beforeend', attachmentChipsHtml(attachments, false));
      }
      if (queued || opts.steering) {
        bubble.classList.add(opts.steering ? 'msg-steering' : 'msg-queued');
        var actions = bubble.querySelector('.msg-actions');
        var chip = opts.steering
          ? '<span class="queue-chip steer-chip">steering</span>'
          : '<span class="queue-chip">queued</span>';
        if (actions) actions.insertAdjacentHTML('afterbegin', chip);
        else bubble.querySelector('.bubble').insertAdjacentHTML('beforeend', chip);
      }
      // Match history layout: user bubble lives inside a centered .turn column.
      const turn = document.createElement('div');
      turn.className = 'turn';
      var queryIndex = nextQueryIndex();
      var queryId = 'chat-query-' + queryIndex;
      turn.dataset.queryId = queryId;
      turn.dataset.queryIndex = String(queryIndex);
      turn.appendChild(bubble);
      scroll.appendChild(turn);
      wrap.dispatchEvent(new CustomEvent('tomo:user-turn', {
        detail: {
          turn: turn,
          queryId: queryId,
          queryIndex: queryIndex,
          text: value || '',
        },
      }));
      atBottom();
      return bubble;
    }

    function markBubbleDequeued(el) {
      if (!el) return;
      el.classList.remove('msg-queued');
      el.classList.remove('msg-steering');
      const chip = el.querySelector('.queue-chip');
      if (chip) chip.remove();
    }

    function removeQueuedBubble(el) {
      if (!el) return;
      var t = el.closest ? el.closest('.turn') : null;
      if (t) {
        notifyUserTurnRemoved(t);
        t.remove();
      }
      else el.remove();
    }

    /**
     * Merge local queue + live composer text into the RUNNING turn (kimi steer).
     * Triggered when the user presses Enter while messages are already queued.
     */
    async function steerQueued(value, attachmentIds, attachments) {
      var parts = [];
      var attachIds = [];
      var attachMeta = [];
      var seenAtt = {};
      while (messageQueue.length) {
        var q = messageQueue.shift();
        if (q && q.text && String(q.text).trim()) parts.push(String(q.text).trim());
        (q.attachmentIds || []).forEach(function (id) {
          if (id && !seenAtt[id]) { seenAtt[id] = true; attachIds.push(id); }
        });
        (q.attachments || []).forEach(function (a) {
          if (a && a.id && !seenAtt['meta:' + a.id]) {
            seenAtt['meta:' + a.id] = true;
            attachMeta.push(a);
          }
        });
        removeQueuedBubble(q && q.el);
      }
      if (value && String(value).trim()) parts.push(String(value).trim());
      (attachmentIds || []).forEach(function (id) {
        if (id && !seenAtt[id]) { seenAtt[id] = true; attachIds.push(id); }
      });
      (attachments || []).forEach(function (a) {
        if (a && a.id && !seenAtt['meta:' + a.id]) {
          seenAtt['meta:' + a.id] = true;
          attachMeta.push(a);
        }
      });
      var merged = parts.join('\n\n');
      if (!merged && !attachIds.length) {
        syncBusyStatus();
        return false;
      }
      var sid = currentSessionId();
      if (!sid) {
        Tomo.toast('No session to steer into', 'err');
        enqueueMessage(merged, attachIds, attachMeta);
        return false;
      }
      var el = appendUserBubble(merged, false, attachMeta, { steering: true });
      syncBusyStatus();
      try {
        var data = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/chat/steer',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: merged,
              attachment_ids: attachIds,
            }),
          }
        );
        if (!data || !data.accepted) {
          throw new Error((data && data.reason) || 'Steer rejected');
        }
        markBubbleDequeued(el);
        if (window.Tomo && Tomo.toast) {
          Tomo.toast('Steering into current turn…', 'ok');
        }
        setStatus('amber', 'busy · steering');
        return true;
      } catch (e) {
        removeQueuedBubble(el);
        // Fall back to queue so the text is not lost; drain after turn ends.
        enqueueMessage(merged, attachIds, attachMeta);
        if (window.Tomo && Tomo.toast) {
          Tomo.toast((e && e.message) || 'Could not steer — queued instead', 'err');
        }
        return false;
      }
    }

    function closeStream() {
      if (es) { es.close(); es = null; }
      // Do not clear sending here — finishTurn owns the queue drain.
    }

    function hitlHost(turnEl) {
      var host = turnEl || scroll.querySelector('.turn:last-child');
      if (host) return host;
      host = document.createElement('div');
      host.className = 'turn';
      scroll.appendChild(host);
      return host;
    }

    function bindHitl(es, turnEl, onEvent) {
      if (!window.TomoHitl || !TomoHitl.bindStream) return;
      TomoHitl.bindStream(es, {
        turn: hitlHost(turnEl),
        scroll: scroll,
        onEvent: onEvent,
        clearPending: null,
        setBusy: function () { setStatus('amber', busyStatusLabel()); },
      });
    }

    function rehydratePendingHitl(turnEl) {
      if (!window.TomoHitl || !TomoHitl.rehydrate) return Promise.resolve(false);
      return TomoHitl.rehydrate(currentSessionId(), hitlHost(turnEl), scroll).then(function (needs) {
        // Keep busy chrome when open HITL cards or an in-flight turn remain.
        if (needs) setStatus('amber', busyStatusLabel());
        return needs;
      });
    }

    function finishTurn() {
      if (es) { es.close(); es = null; }
      sending = false;
      syncGeneratingUi();
      wrap.dispatchEvent(new CustomEvent('tomo:chat-done'));

      // Keep pin briefly after the stream ends (layout may still settle).
      if (Tomo.stickScrollBottom) {
        Tomo.stickScrollBottom(scroll, { holdMs: 8000, times: [0, 50, 200, 500, 1500, 3000] });
      }

      if (messageQueue.length) {
        const next = messageQueue.shift();
        markBubbleDequeued(next.el);
        syncBusyStatus();
        // Fire-and-forget next turn (async).
        // Bubble was already shown when enqueued (or on the failed concurrent try).
        startTurn(next.text, {
          alreadyBubbled: true,
          bubbleEl: next.el,
          attachmentIds: next.attachmentIds || [],
          attachments: next.attachments || [],
        });
        return;
      }
      setStatus('ok', 'online');
      refreshSendBtn();
      input.focus();
    }

    /**
     * Stop the in-flight server turn and clear any local queue.
     * Disconnect alone does not cancel the background agent loop.
     */
    async function stopTurn() {
      if (!sending && !es) return;
      closeMoreMenu();
      // Drop queued follow-ups — stop means stop.
      while (messageQueue.length) {
        var dropped = messageQueue.shift();
        if (dropped && dropped.el) {
          var t = dropped.el.closest ? dropped.el.closest('.turn') : null;
          if (t) {
            notifyUserTurnRemoved(t);
            t.remove();
          }
          else dropped.el.remove();
        }
      }
      var url = stopUrl();
      try {
        if (url) await Tomo.api(url, { method: 'POST' });
      } catch (e) {
        if (window.Tomo && Tomo.toast) {
          Tomo.toast((e && e.message) || 'Could not stop', 'err');
        }
      }
      if (es) { es.close(); es = null; }
      sending = false;
      delete wrap.dataset.liveStream;
      wrap.dispatchEvent(new CustomEvent('tomo:turn-end', { bubbles: true }));
      wrap.dispatchEvent(new CustomEvent('tomo:chat-done'));
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

    function streamUrl(text, attachmentIds) {
      const sid = currentSessionId();
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/stream';
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/stream';
    }

    function streamBody(text, attachmentIds) {
      return {
        message: text || '',
        attachment_ids: attachmentIds || [],
      };
    }

    function listenUrl() {
      const sid = currentSessionId();
      if (!sid) return '';
      // No message → join active turn (replay + live) or idle heartbeats.
      return '/api/sessions/' + encodeURIComponent(sid) + '/chat/stream?user_id=' + encodeURIComponent(userId) + '&after=0';
    }

    function clearUrl() {
      const sid = currentSessionId();
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/clear';
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/clear?user_id=' + encodeURIComponent(userId);
    }

    function stopUrl() {
      const sid = currentSessionId();
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/stop';
      }
      if (agentId) {
        return '/api/agents/' + encodeURIComponent(agentId) + '/chat/stop';
      }
      return '';
    }

    async function dispatchUiAction(action) {
      var sid = currentSessionId();
      if (!sid || !action) return send('[UI action]\n' + JSON.stringify(action || {}));
      try {
        var result = await Tomo.api(
          '/api/sessions/' + encodeURIComponent(sid) + '/ui-actions',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(action),
          }
        );
        if (result && (result.mode === 'started' || result.mode === 'steer') && !sending && !es) {
          resumeActiveTurn();
        }
        return result;
      } catch (e) {
        if (window.Tomo && Tomo.toast) {
          Tomo.toast((e && e.message) || 'Could not dispatch UI action', 'err');
        }
        return null;
      }
    }

    async function ensureSession() {
      const existing = currentSessionId();
      if (existing) return existing;
      if (agentId) return '';
      const agents = pendingAgentIds();
      if (!agents.length) throw new Error('No agents');
      var workplaceId = (wrap.dataset.workplaceId || '').trim();
      const data = await Tomo.api('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_ids: agents,
          user_id: userId,
          workplace_id: workplaceId || null,
        }),
      });
      if (!data || !data.session_id) throw new Error('No session');
      wrap.dataset.sessionId = data.session_id;
      if (data.workplace_id) wrap.dataset.workplaceId = data.workplace_id;
      delete wrap.dataset.pendingAgents;
      wrap.dispatchEvent(new CustomEvent('tomo:session-created', {
        detail: {
          session_id: data.session_id,
          agent_ids: agents,
          workplace_id: data.workplace_id || workplaceId || '',
        },
      }));
      refreshApprovalMode();
      refreshReasoningEffort();
      return data.session_id;
    }

    /**
     * Re-attach to an in-flight turn via listen SSE (GET). Used after page
     * refresh and when the live POST stream dies while the agent keeps running.
     */
    function reconnectStream(turnEl) {
      var url = listenUrl();
      if (!url) {
        sending = false;
        syncGeneratingUi();
        setStatus('ok', 'online');
        refreshSendBtn();
        return false;
      }
      if (es) {
        try { es.close(); } catch (_) {}
        es = null;
      }
      var turn = hitlHost(turnEl);
      rehydratePendingHitl(turn);
      sending = true;
      wrap.dataset.liveStream = '1';
      setStatus('amber', busyStatusLabel());
      refreshSendBtn();
      es = new EventSource(url);
      if (!window.TomoTurnStream || !TomoTurnStream.attach) {
        console.error('[tomo] TomoTurnStream missing');
        closeStream();
        sending = false;
        syncGeneratingUi();
        return false;
      }
      TomoTurnStream.attach(es, turnStreamCtx('resume', turn, {}));
      return true;
    }

    function turnStreamCtx(mode, turn, extra) {
      extra = extra || {};
      return Object.assign({
        mode: mode,
        wrap: wrap,
        scroll: scroll,
        turn: turn,
        agentId: agentId || '',
        defaultAgentName: defaultAgentName,
        esc: esc,
        bubbleHtml: bubbleHtml,
        agentColor: agentColor,
        setMarkdown: setMarkdown,
        atBottom: atBottom,
        setStatus: setStatus,
        busyStatusLabel: busyStatusLabel,
        onApproval: paintApprovalMode,
        refreshSendBtn: refreshSendBtn,
        closeStream: closeStream,
        finishTurn: finishTurn,
        reconnectStream: reconnectStream,
        scheduleQueueDrain: scheduleQueueDrain,
        setSending: function (v) { sending = !!v; syncGeneratingUi(); },
        getSending: function () { return sending; },
        messageQueue: messageQueue,
        sendMessage: send,
        dispatchUiAction: dispatchUiAction,
        currentSessionId: currentSessionId,
        onBindHitl: function (stream, turnEl, onEvent) {
          bindHitl(stream, turnEl, onEvent);
        },
      }, extra);
    }

    function streamTurn(text, turnEl, attachmentIds) {
      var oldPanel = wrap.querySelector('.subagent-inspector, .detail-panel');
      if (oldPanel) oldPanel.remove();

      wrap.dataset.liveStream = '1';
      wrap.dispatchEvent(new CustomEvent('tomo:turn-start', { bubbles: true }));

      var turn = turnEl;
      if (!turn || !turn.classList || !turn.classList.contains('turn')) {
        turn = document.createElement('div');
        turn.className = 'turn';
        scroll.appendChild(turn);
      }

      // Pin for the entire streaming turn — new .turn nodes, images, and
      // artifact panels are observed by MutationObserver inside stickScrollBottom.
      if (Tomo.stickScrollBottom) {
        Tomo.stickScrollBottom(scroll, { holdMs: 120000, times: [50, 200, 500, 1000, 2000, 4000] });
      }
      atBottom();

      var attachIds = attachmentIds || [];
      es = postEventSource(streamUrl(text, attachIds), streamBody(text, attachIds));
      if (!window.TomoTurnStream || !TomoTurnStream.attach) {
        console.error('[tomo] TomoTurnStream missing');
        closeStream();
        finishTurn();
        return;
      }
      TomoTurnStream.attach(es, turnStreamCtx('live', turn, {
        text: text || '',
        attachIds: attachIds,
      }));
    }

    /**
     * @param {string} value
     * @param {{alreadyBubbled?: boolean, bubbleEl?: Element|null, attachmentIds?: string[], attachments?: {id:string,name:string,size:number}[]}} [opts]
     */
    async function startTurn(value, opts) {
      opts = opts || {};
      var attachIds = opts.attachmentIds || [];
      var attachMeta = opts.attachments || [];
      if (!value && !attachIds.length) return;
      if (sending) {
        enqueueMessage(value, attachIds, attachMeta);
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
        if (messageQueue.length) finishTurn();
        return;
      }
      var userBubble = opts.bubbleEl || null;
      if (!opts.alreadyBubbled) {
        userBubble = appendUserBubble(value, false, attachMeta);
      }
      var turnEl = userBubble && userBubble.closest ? userBubble.closest('.turn') : null;
      streamTurn(value || (attachMeta.length ? 'Please review the attached file(s).' : ''), turnEl, attachIds);
    }

    function enqueueMessage(value, attachmentIds, attachments) {
      if (messageQueue.length >= MAX_QUEUE) {
        Tomo.toast('Queue full (max ' + MAX_QUEUE + ' messages). Wait for the current turn.', 'err');
        return false;
      }
      var attachIds = attachmentIds || [];
      var attachMeta = attachments || [];
      const el = appendUserBubble(value, true, attachMeta);
      messageQueue.push({ text: value, el: el, attachmentIds: attachIds, attachments: attachMeta });
      setStatus('amber', busyStatusLabel());
      return true;
    }

    async function send(text) {
      const value = (text != null ? String(text) : input.value).trim();
      const attachMeta = uploadedAttachments.map(function (a) {
        return { id: a.id, name: a.name, size: a.size };
      });
      const attachIds = attachMeta.map(function (a) { return a.id; });
      var hasQueue = messageQueue.length > 0;
      // Empty Enter is allowed when steering an existing queue into the turn.
      if (!value && !attachIds.length && !(sending && hasQueue)) return;
      if (uploading) {
        Tomo.toast('Wait for uploads to finish', 'err');
        return;
      }
      hidePopups();
      closeMoreMenu();
      input.value = '';
      resize();
      uploadedAttachments = [];
      renderAttachmentPreview();
      refreshSendBtn();
      if (sending && hasQueue) {
        await steerQueued(value, attachIds, attachMeta);
        return;
      }
      if (sending) {
        enqueueMessage(value, attachIds, attachMeta);
        return;
      }
      await startTurn(value, { attachmentIds: attachIds, attachments: attachMeta });
    }

    input.addEventListener('input', function () {
      refreshSendBtn();
      resize();
      updateSlash();
      updateMentions();
    });

    if (attachBtn && attachInput) {
      attachBtn.addEventListener('click', function () { attachInput.click(); });
      attachInput.addEventListener('change', function () {
        if (attachInput.files && attachInput.files.length) {
          uploadFiles(Array.from(attachInput.files));
          attachInput.value = '';
        }
      });
    }

    var dragTarget = composerEl || wrap;
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function (name) {
      dragTarget.addEventListener(name, function (e) { e.preventDefault(); e.stopPropagation(); });
    });
    dragTarget.addEventListener('dragenter', function () { dragTarget.classList.add('dragover'); });
    dragTarget.addEventListener('dragover', function () { dragTarget.classList.add('dragover'); });
    dragTarget.addEventListener('dragleave', function (e) {
      if (!dragTarget.contains(e.relatedTarget)) dragTarget.classList.remove('dragover');
    });
    dragTarget.addEventListener('drop', function (e) {
      dragTarget.classList.remove('dragover');
      const files = Array.from(e.dataTransfer.files || []);
      if (files.length) uploadFiles(files);
    });
    // Ctrl+V / Cmd+V one or more images straight into the composer — same
    // upload path as drag-drop/the attach button, so paste is additive, not
    // a replacement for either.
    input.addEventListener('paste', function (e) {
      const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
      const images = items
        .filter(function (it) { return it.kind === 'file' && it.type.indexOf('image/') === 0; })
        .map(function (it) { return it.getAsFile(); })
        .filter(Boolean);
      if (!images.length) return; // let normal text paste through untouched
      e.preventDefault();
      uploadFiles(images);
    });
    input.addEventListener('keydown', function (e) {
      if (slashOpen && slashMatches.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          slashIndex = (slashIndex + 1) % slashMatches.length;
          renderSlashMenu();
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          slashIndex = (slashIndex - 1 + slashMatches.length) % slashMatches.length;
          renderSlashMenu();
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          insertSlash(slashIndex);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          hideSlash();
          return;
        }
      }
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
      // Ctrl/Cmd+S — steer live text (+ any queue) into the running turn.
      if (e.key === 's' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
        if (sending) {
          e.preventDefault();
          (async function () {
            var value = input.value.trim();
            var attachMeta = uploadedAttachments.map(function (a) {
              return { id: a.id, name: a.name, size: a.size };
            });
            var attachIds = attachMeta.map(function (a) { return a.id; });
            if (!value && !attachIds.length && !messageQueue.length) return;
            if (uploading) {
              Tomo.toast('Wait for uploads to finish', 'err');
              return;
            }
            hidePopups();
            input.value = '';
            resize();
            uploadedAttachments = [];
            renderAttachmentPreview();
            refreshSendBtn();
            // No queue yet: park live text as a headless queue entry, then steer.
            if (!messageQueue.length && (value || attachIds.length)) {
              messageQueue.push({
                text: value,
                el: null,
                attachmentIds: attachIds,
                attachments: attachMeta,
              });
              value = '';
              attachIds = [];
              attachMeta = [];
            }
            await steerQueued(value, attachIds, attachMeta);
          })();
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener('blur', function () {
      // Delay so mousedown on menu still fires.
      setTimeout(hidePopups, 150);
    });
    sendBtn.addEventListener('click', function () { send(); });
    if (stopBtn) {
      stopBtn.addEventListener('click', function () { stopTurn(); });
    }
    resize();
    syncGeneratingUi();

    function flashActBtn(btn) {
      if (!btn) return;
      var prev = btn.innerHTML;
      btn.innerHTML = ICON_CHECK;
      btn.classList.add('ok');
      setTimeout(function () {
        btn.innerHTML = prev;
        btn.classList.remove('ok');
      }, 1200);
    }

    function copyMsgText(msg, btn) {
      var text = msgPlainText(msg);
      if (!text) return;
      function done() { flashActBtn(btn); }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else {
        fallback();
      }
    }

    function editUserMsg(msg) {
      var text = msgPlainText(msg);
      input.value = text;
      resize();
      refreshSendBtn();
      input.focus();
      try {
        var len = input.value.length;
        input.setSelectionRange(len, len);
      } catch (e) {}
    }

    function regenFromMsg(msg) {
      var turn = msg.closest('.turn');
      var userMsg = turn && turn.querySelector('.msg.user');
      if (!userMsg && turn && turn.previousElementSibling) {
        userMsg = turn.previousElementSibling.querySelector('.msg.user');
      }
      var text = msgPlainText(userMsg);
      if (!text) {
        Tomo.toast('No user message to regenerate from', 'err');
        return;
      }
      if (sending) {
        Tomo.toast('Wait for the current turn to finish', 'err');
        return;
      }
      send(text);
    }

    scroll.addEventListener('click', function (e) {
      var btn = e.target.closest('.msg-act');
      if (!btn || !scroll.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      var msg = btn.closest('.msg');
      if (!msg) return;
      var act = btn.getAttribute('data-act');
      if (act === 'copy') copyMsgText(msg, btn);
      else if (act === 'edit') editUserMsg(msg);
      else if (act === 'regen') regenFromMsg(msg);
    });

    ensureMsgActions(scroll);
    wrap.addEventListener('tomo:turn-end', function () {
      ensureMsgActions(scroll);
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', async function () {
        if (!confirm('Clear this conversation?')) return;
        messageQueue = [];
        if (es) { es.close(); es = null; }
        sending = false;
        syncGeneratingUi();
        if (window.Tomo && Tomo.clearTodoDock) Tomo.clearTodoDock(wrap);
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

    if (window.TomoContextUsage && TomoContextUsage.init) {
      TomoContextUsage.init(wrap);
    }

    /**
     * Re-attach to an in-flight session turn after page refresh.
     * History already rendered the past; this joins listen-mode SSE so
     * status stays busy and unpaired tool cards keep updating live.
     * @returns {boolean} true if a resume stream was opened
     */
    function resumeActiveTurn() {
      var sid = currentSessionId();
      var url = listenUrl();
      if (!sid || !url) return false;
      // Already attached to a live stream — leave it alone.
      if (es && sending) return true;

      return reconnectStream(null);
    }

    return {
      destroy: function () {
        messageQueue = [];
        if (es) { es.close(); es = null; }
        sending = false;
        syncGeneratingUi();
        document.removeEventListener('click', onReasoningDocumentClick);
        document.removeEventListener('keydown', onReasoningEscape);
        document.removeEventListener('pointerdown', onMoreDocumentPointerDown);
        document.removeEventListener('keydown', onMoreEscape);
        closeMoreMenu();
        delete wrap.dataset.liveStream;
      },
      send: send,
      uiAction: dispatchUiAction,
      stop: stopTurn,
      resume: resumeActiveTurn,
      reconnect: reconnectStream,
      rehydratePending: rehydratePendingHitl,
    };
  }

  window.TomoChat = {
    init: initChat,
    renderMarkdown: renderMarkdown,
    setMarkdown: setMarkdown,
    ensureMsgActions: ensureMsgActions,
    msgActionsHtml: msgActionsHtml,
  };

  document.querySelectorAll('.chat-wrap').forEach(function (wrap) {
    var handle = initChat(wrap);
    if (!handle) return;
    // Agent detail / hard refresh: restore HITL cards and re-attach mid-turn stream.
    if (handle.rehydratePending) {
      handle.rehydratePending().then(function (needsResume) {
        if (needsResume && handle.resume) {
          handle.resume();
          return;
        }
        var scrollEl = wrap.querySelector('.chat-scroll');
        var loadingTools = scrollEl && scrollEl.querySelectorAll('.tool.loading');
        if (loadingTools && loadingTools.length && handle.resume) handle.resume();
      });
    }
  });
})();

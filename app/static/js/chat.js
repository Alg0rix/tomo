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
    const slashMenu = wrap.querySelector('.slash-menu');
    const attachBtn = wrap.querySelector('.attach-btn');
    const attachInput = wrap.querySelector('.attachment-input');
    const attachPreview = wrap.querySelector('.attachment-preview');
    const composerEl = wrap.querySelector('.composer');
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

    function hideSlash() {
      slashOpen = false;
      slashMatches = [];
      if (slashMenu) {
        slashMenu.classList.add('hidden');
        slashMenu.innerHTML = '';
      }
    }

    function hidePopups() {
      hideMentions();
      hideSlash();
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
      var all = skillsCache || [];
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
        var sid = s.id || s.name || '';
        var label = '/' + sid;
        var desc = s.description || s.name || '';
        return '<button type="button" class="slash-item' + active + '" data-idx="' + i + '" role="option">' +
          '<span class="slash-name">' + esc(label) + '</span>' +
          '<span class="slash-desc">' + esc(desc) + '</span></button>';
      }).join('');
      slashMenu.classList.remove('hidden');
      slashOpen = true;
      slashMenu.querySelectorAll('.slash-item').forEach(function (btn) {
        btn.addEventListener('mousedown', function (e) {
          e.preventDefault();
          insertSlash(parseInt(btn.dataset.idx, 10) || 0);
        });
      });
    }

    function updateSlash() {
      if (!slashMenu || !input) return;
      var val = input.value;
      // Kimi-style: only when the whole input is a single /token (no space yet).
      if (!(val.startsWith('/') && val.indexOf(' ') < 0)) {
        hideSlash();
        return;
      }
      hideMentions();
      // Re-fetch when opening so mid-session installs show up.
      ensureSkills(function () {
        // Re-check — input may have changed while fetch was in flight.
        var cur = input.value;
        if (!(cur.startsWith('/') && cur.indexOf(' ') < 0)) {
          hideSlash();
          return;
        }
        slashMatches = filterSkills(cur);
        slashIndex = 0;
        renderSlashMenu();
      }, !skillsCache || (slashMenu && slashMenu.classList.contains('hidden')));
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

    function atBottom() { scroll.scrollTop = scroll.scrollHeight; }
    function setStatus(badge, label) {
      if (!statusEl) return;
      statusEl.className = 'badge ' + badge;
      statusEl.innerHTML = '<span class="pulse"></span>' + esc(label);
    }

    function refreshSendBtn() {
      // While a turn is running, send is still allowed so messages enqueue.
      sendBtn.disabled = uploading || (!input.value.trim() && !uploadedAttachments.length);
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
    atBottom();

    function resize() {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }

    function appendUserBubble(value, queued, attachments) {
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const u = document.createElement('div');
      u.innerHTML = bubbleHtml('user', defaultAgentName);
      const bubble = u.firstElementChild;
      const body = bubble.querySelector('.bubble-body');
      body.innerHTML = highlightMentions(value || '');
      if (attachments && attachments.length) {
        body.insertAdjacentHTML('beforeend', attachmentChipsHtml(attachments, false));
      }
      if (queued) {
        bubble.classList.add('msg-queued');
        const who = bubble.querySelector('.who');
        if (who) {
          who.innerHTML = 'You <span class="queue-chip">queued</span>';
        }
      }
      // Match history layout: user bubble lives inside a centered .turn column.
      const turn = document.createElement('div');
      turn.className = 'turn';
      turn.appendChild(bubble);
      scroll.appendChild(turn);
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
      const params = new URLSearchParams();
      params.set('user_id', userId);
      params.set('message', text);
      (attachmentIds || []).forEach(function (id) { params.append('attachment_ids', id); });
      if (sid) {
        return '/api/sessions/' + encodeURIComponent(sid) + '/chat/stream?' + params.toString();
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/stream?' + params.toString();
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
      return data.session_id;
    }

    function streamTurn(text, turnEl, attachmentIds) {
      // Clean up any leftover detail panel from a previous turn.
      var oldPanel = wrap.querySelector('.subagent-inspector, .detail-panel');
      if (oldPanel) oldPanel.remove();

      wrap.dataset.liveStream = '1';
      wrap.dispatchEvent(new CustomEvent('tomo:turn-start', { bubbles: true }));

      // Reuse the turn that already holds this user bubble (matches history layout).
      let turn = turnEl;
      if (!turn || !turn.classList || !turn.classList.contains('turn')) {
        turn = document.createElement('div');
        turn.className = 'turn';
        scroll.appendChild(turn);
      }
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
      const attachIds = attachmentIds || [];

      es = new EventSource(streamUrl(text, attachIds));

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
        'subagent_start', 'subagent_done', 'approval_required', 'clarify_required',
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
        closed = true;
        closeStream();
        delete wrap.dataset.liveStream;
        wrap.dispatchEvent(new CustomEvent('tomo:turn-end', { bubbles: true }));
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
          '</div>' +
          '<span class="si-open-hint" aria-hidden="true">inspect →</span>';
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
        var color = agentColor(aid);
        var letter = esc((name || '?').slice(0, 1).toUpperCase());
        var status = buf.status || 'running';

        var panel = wrap.querySelector('.subagent-inspector');
        if (!panel) {
          panel = document.createElement('aside');
          panel.className = 'subagent-inspector';
          panel.setAttribute('role', 'complementary');
          panel.setAttribute('aria-label', 'Subagent inspector');
          wrap.appendChild(panel);
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
            var cColor = agentColor(id);
            var cLetter = esc((b.name || id || '?').slice(0, 1).toUpperCase());
            var label = base;
            if (nameCounts[base] > 1) label += ' #' + nameSeen[base];
            pill.innerHTML =
              '<span class="av" style="background:' + cColor + '">' + cLetter + '</span>' +
              '<span>' + esc(label) + '</span>' +
              '<span class="dot ' + esc(st) + '"></span>';
            pill.addEventListener('click', function () { openDetailPanel(id); });
            switcher.appendChild(pill);
          });
          panel.appendChild(switcher);
        }

        var body = document.createElement('div');
        body.className = 'si-body';
        panel.appendChild(body);

        if (!buf.events.length) {
          body.innerHTML = '<div class="si-empty">No steps yet — waiting for this agent to run.</div>';
        } else {
          var tl = document.createElement('div');
          tl.className = 'si-timeline';
          body.appendChild(tl);
          buf.events.forEach(function (ev) {
            renderEventInDetail(ev.kind, ev.data, aid);
          });
        }

        head.querySelector('.si-close').addEventListener('click', closeDetailPanel);
        wrap.querySelectorAll('.swarm-row').forEach(function (r) {
          r.classList.toggle('selected', r.dataset.agentId === aid);
        });
        requestAnimationFrame(function () { body.scrollTop = body.scrollHeight; });
      }

      function closeDetailPanel() {
        activeDetailAgent = null;
        wrap.querySelectorAll('.swarm-row.selected').forEach(function (r) {
          r.classList.remove('selected');
        });
        var panel = wrap.querySelector('.subagent-inspector');
        detailPanel = null;
        if (panel) panel.remove();
      }

      function renderEventInDetail(kind, data, aid) {
        if (!detailPanel) return;
        var body = detailPanel.querySelector('.si-body');
        if (!body) return;
        if (window.Tomo && Tomo.renderInspectorStep) {
          Tomo.renderInspectorStep(body, kind, data);
          body.scrollTop = body.scrollHeight;
        }
      }

      function makeToolCollapsible(card) {
        if (window.Tomo && Tomo.wireToolCard) Tomo.wireToolCard(card);
      }

      function buildToolCard(d) {
        if (window.Tomo && Tomo.buildToolCard) {
          return Tomo.buildToolCard({ tool: d.tool || 'tool', args: d.args || {}, running: true });
        }
        var tool = d.tool || 'tool';
        var args = d.args || {};
        var card = document.createElement('div');
        card.className = 'tool loading';
        card.innerHTML =
          '<button type="button" class="tool-head">' +
            '<span class="tstatus"></span><span class="tname">' + esc(tool) + '</span> ' +
            '<span class="targs"></span><span class="tchip"></span><span class="chevron"></span>' +
          '</button><div class="tool-body"><pre class="tres"></pre></div>';
        card._res = card.querySelector('.tres');
        card._chip = card.querySelector('.tchip');
        makeToolCollapsible(card);
        return card;
      }

      function buildApprovalCard(d) {
        var card = document.createElement('div');
        card.className = 'hitl-card approval-card';
        card.dataset.id = d.id || '';
        var findings = (d.findings || []).map(function (f) {
          return '<li>' + esc(f.description || f.kind || '') + '</li>';
        }).join('');
        var preview = '';
        try {
          preview = typeof d.args_preview === 'string'
            ? d.args_preview
            : JSON.stringify(d.args_preview || {}, null, 2);
        } catch (_) {
          preview = String(d.args_preview || '');
        }
        var choices = d.choices || ['once', 'session', 'always', 'deny'];
        var labels = { once: 'Once', session: 'Session', always: 'Always', deny: 'Deny' };
        var btns = choices.map(function (c) {
          return '<button type="button" class="hitl-btn" data-choice="' + esc(c) + '">' +
            esc(labels[c] || c) + '</button>';
        }).join('');
        card.innerHTML =
          '<div class="hitl-title">Approval required · ' + esc(d.tool || 'tool') + '</div>' +
          '<div class="hitl-desc">' + esc(d.description || '') + '</div>' +
          (findings ? '<ul class="hitl-findings">' + findings + '</ul>' : '') +
          '<pre class="hitl-preview">' + esc(preview).slice(0, 800) + '</pre>' +
          '<div class="hitl-actions">' + btns + '</div>';
        card.querySelectorAll('.hitl-btn').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var choice = btn.getAttribute('data-choice');
            card.classList.add('resolved');
            card.querySelectorAll('.hitl-btn').forEach(function (b) { b.disabled = true; });
            fetch('/api/approvals/' + encodeURIComponent(d.id), {
              method: 'POST',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ choice: choice }),
            }).catch(function () {});
          });
        });
        return card;
      }

      function buildClarifyCard(d) {
        var card = document.createElement('div');
        card.className = 'hitl-card clarify-card';
        card.dataset.id = d.id || '';
        var choices = d.choices || [];
        var btns = choices.map(function (c, i) {
          return '<button type="button" class="hitl-btn" data-answer="' + esc(c) + '">' +
            esc(c) + '</button>';
        }).join('');
        card.innerHTML =
          '<div class="hitl-title">Question</div>' +
          '<div class="hitl-desc">' + esc(d.question || '') + '</div>' +
          '<div class="hitl-actions">' + btns + '</div>' +
          '<div class="hitl-other">' +
            '<input type="text" class="hitl-input" placeholder="Other…" />' +
            '<button type="button" class="hitl-btn hitl-send">Send</button>' +
          '</div>';
        function submit(answer) {
          if (!answer) return;
          card.classList.add('resolved');
          card.querySelectorAll('button,input').forEach(function (el) { el.disabled = true; });
          fetch('/api/clarify/' + encodeURIComponent(d.id), {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer: answer }),
          }).catch(function () {});
        }
        card.querySelectorAll('.hitl-btn[data-answer]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            submit(btn.getAttribute('data-answer') || '');
          });
        });
        var sendBtn = card.querySelector('.hitl-send');
        var inputEl = card.querySelector('.hitl-input');
        if (sendBtn && inputEl) {
          sendBtn.addEventListener('click', function () { submit(inputEl.value.trim()); });
          inputEl.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') {
              ev.preventDefault();
              submit(inputEl.value.trim());
            }
          });
        }
        return card;
      }

      es.addEventListener('state', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (d.busy) {
          setStatus('amber', busyStatusLabel());
        }
        // End only when the active turn agent signals idle — not other swarm members.
        if (!d.busy && turnActive) {
          var who = d.agent_id || '';
          if (!who || who === turnAgentId || who === agentId) endTurn();
        } else if (!d.busy && !sending) setStatus('ok', 'online');
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
        turn.appendChild(buildToolCard(d));
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
        if (last) {
          var resultText = typeof d.result === 'string' ? d.result : JSON.stringify(d.result);
          if (window.Tomo && Tomo.finishToolCard) {
            Tomo.finishToolCard(last, resultText, !!d.error);
          } else if (last._res) {
            last._res.textContent = resultText;
            last.classList.remove('loading');
          }
        }
        // Keep typing indicator after tool so UI does not look frozen mid-turn.
        if (!asstEl && !pendingEl) showPending();
        atBottom();
      });
      es.addEventListener('approval_required', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        clearPending();
        turn.appendChild(buildApprovalCard(d));
        atBottom();
      });
      es.addEventListener('clarify_required', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        clearPending();
        turn.appendChild(buildClarifyCard(d));
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
            messageQueue.unshift({ text: text, el: null, attachmentIds: attachIds });
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
        // Heartbeats are keep-alive signals during long operations
        // (subagent LLM calls, tool execution). Do NOT end the turn.
        bumpActivity();
      });
      es.addEventListener('auth_expired', function () { window.location.href = '/login'; });
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
      if (!value && !attachIds.length) return;
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
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener('blur', function () {
      // Delay so mousedown on menu still fires.
      setTimeout(hidePopups, 150);
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
      const sid = currentSessionId();
      const url = listenUrl();
      if (!sid || !url || sending || es) return false;

      const turns = scroll.querySelectorAll('.turn');
      const turn = turns[turns.length - 1];
      if (!turn) return false;

      let closed = false;
      let sawTurnEvent = false;
      let turnAgentName = defaultAgentName;
      let turnAgentId = agentId || '';
      let thinkEl = null;
      let asstEl = null;
      let asstBody = null;
      let pendingEl = null;
      let raw = '';
      let idleTimer = null;
      let hardTimer = null;
      const IDLE_MS = 180000;
      const HARD_MS = 720000;

      // Skip replayed tool/tool_result events already represented in history.
      let skipTools = turn.querySelectorAll('.tool').length;
      let skipResults = 0;
      turn.querySelectorAll('.tool').forEach(function (c) {
        if (c._res && c._res.textContent) skipResults++;
      });
      let toolSeen = 0;
      let resultSeen = 0;

      const subagentSet = new Set();
      turn.querySelectorAll('.swarm-row[data-agent-id]').forEach(function (row) {
        if (row.dataset.agentId) subagentSet.add(row.dataset.agentId);
      });

      function clearWatchdogs() {
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
        if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
      }

      function armIdle(ms) {
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(function () {
          if (closed) return;
          endResume();
        }, ms || IDLE_MS);
      }

      function bumpActivity() {
        armIdle(IDLE_MS);
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
        // Prefer an existing assistant bubble from history (partial refresh).
        const existing = turn.querySelector('.msg.assistant');
        if (existing) {
          asstEl = existing;
          asstBody = existing.querySelector('.bubble-body');
          raw = (asstBody && asstBody.textContent) || '';
          return asstEl;
        }
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

      function adoptAgent(id, name) {
        if (id) turnAgentId = id;
        if (name) turnAgentName = name;
      }

      function clearToolLoading() {
        turn.querySelectorAll('.tool.loading').forEach(function (c) {
          c.classList.remove('loading');
        });
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
            '<span class="tstatus"></span><span class="tname">' + esc(tool) + '</span>' +
            '<span class="targs"></span><span class="tchip"></span><span class="chevron"></span>' +
          '</button><div class="tool-body"><pre class="tres"></pre></div>';
        card._res = card.querySelector('.tres');
        card._chip = card.querySelector('.tchip');
        makeToolCollapsible(card);
        return card;
      }

      function applyToolResult(d) {
        const cards = turn.querySelectorAll('.tool');
        const last = cards[cards.length - 1];
        if (last) {
          var resultText = typeof d.result === 'string' ? d.result : JSON.stringify(d.result);
          if (window.Tomo && Tomo.finishToolCard) {
            Tomo.finishToolCard(last, resultText, !!d.error);
          } else if (last._res) {
            last._res.textContent = resultText;
            last.classList.remove('loading');
          }
        }
        if (!asstEl && !pendingEl) showPending();
        atBottom();
      }

      function endResume() {
        if (closed) return;
        clearWatchdogs();
        clearPending();
        dropEmptyAssistant();
        closed = true;
        closeStream();
        delete wrap.dataset.liveStream;
        wrap.dispatchEvent(new CustomEvent('tomo:turn-end', { bubbles: true }));
        finishTurn();
      }

      function endIdleResume() {
        // Listen mode hit idle heartbeats — turn already finished (or never ran).
        if (closed) return;
        clearWatchdogs();
        clearToolLoading();
        closed = true;
        closeStream();
        delete wrap.dataset.liveStream;
        sending = false;
        setStatus('ok', 'online');
        refreshSendBtn();
      }

      sending = true;
      wrap.dataset.liveStream = '1';
      setStatus('amber', busyStatusLabel());
      wrap.dispatchEvent(new CustomEvent('tomo:turn-start', { bubbles: true }));
      refreshSendBtn();

      es = new EventSource(url);
      hardTimer = setTimeout(function () {
        if (closed) return;
        endResume();
      }, HARD_MS);
      armIdle(IDLE_MS);

      // If listen mode is only idle heartbeats (turn already finished), stop
      // looking busy once replay would have arrived.
      setTimeout(function () {
        if (!closed && !sawTurnEvent) endIdleResume();
      }, 1000);

      es.addEventListener('state', function (e) {
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        if (d.busy) setStatus('amber', busyStatusLabel());
      });

      es.addEventListener('turn.start', function () {
        sawTurnEvent = true;
        bumpActivity();
        setStatus('amber', busyStatusLabel());
      });

      es.addEventListener('tool', function (e) {
        sawTurnEvent = true;
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        const aid = d.agent_id || '';
        if (aid && subagentSet.has(aid) && aid !== agentId) return;
        toolSeen++;
        if (toolSeen <= skipTools) {
          // Replay of a history tool — keep unpaired card in loading state.
          const cards = turn.querySelectorAll('.tool');
          const card = cards[toolSeen - 1];
          if (card && !(card._res && card._res.textContent)) {
            card.classList.add('loading');
            if (!card.querySelector('.tloading')) {
              const tip = document.createElement('div');
              tip.className = 'tloading';
              tip.textContent = 'running\u2026';
              card.insertBefore(tip, card._res || null);
            }
          }
          return;
        }
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        dropEmptyAssistant();
        turn.appendChild(buildToolCard(d));
        atBottom();
      });

      es.addEventListener('tool_result', function (e) {
        sawTurnEvent = true;
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        const aid = d.agent_id || '';
        if (aid && subagentSet.has(aid) && aid !== agentId) return;
        resultSeen++;
        if (resultSeen <= skipResults) return;
        applyToolResult(d);
      });

      es.addEventListener('thinking', function (e) {
        sawTurnEvent = true;
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        const aid = d.agent_id || '';
        if (aid && subagentSet.has(aid) && aid !== agentId) return;
        adoptAgent(d.agent_id, d.agent);
        clearPending();
        if (!thinkEl) {
          thinkEl = document.createElement('div');
          thinkEl.className = 'thinking';
          turn.appendChild(thinkEl);
        }
        thinkEl.textContent += d.content || '';
        atBottom();
      });

      es.addEventListener('delta', function (e) {
        sawTurnEvent = true;
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        const aid = d.agent_id || '';
        if (aid && subagentSet.has(aid) && aid !== agentId) return;
        adoptAgent(d.agent_id, d.agent);
        const piece = d.content || '';
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
        sawTurnEvent = true;
        bumpActivity();
        const d = JSON.parse(e.data || '{}');
        const aid = d.agent_id || '';
        if (aid && subagentSet.has(aid) && aid !== agentId) return;
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
        endResume();
      });

      es.addEventListener('heartbeat', function () {
        bumpActivity();
        // Heartbeat with no turn events ⇒ idle listen stream (stale busy, etc.).
        if (!sawTurnEvent) endIdleResume();
      });

      es.addEventListener('error', function (e) {
        if (closed) return;
        if (e && e.data) {
          let msg = 'Agent error';
          try {
            const payload = JSON.parse(e.data);
            msg = payload.message || msg;
            const errAgentId = payload.agent_id || '';
            if (errAgentId && subagentSet.has(errAgentId) && errAgentId !== agentId) return;
          } catch (_) {}
          clearPending();
          const tmp = document.createElement('div');
          tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
          const b = tmp.firstElementChild;
          turn.appendChild(b);
          b.querySelector('.bubble-body').innerHTML =
            '<span style="color:var(--danger)">' + esc(msg) + '</span>';
          endResume();
          return;
        }
        // Transport error: EventSource will retry; don't tear down yet if turn active.
        if (sawTurnEvent) return;
        endIdleResume();
      });

      es.addEventListener('auth_expired', function () { window.location.href = '/login'; });
      return true;
    }

    return {
      destroy: function () {
        messageQueue = [];
        if (es) { es.close(); es = null; }
        sending = false;
        delete wrap.dataset.liveStream;
      },
      send: send,
      resume: resumeActiveTurn,
    };
  }

  window.TomoChat = { init: initChat, renderMarkdown: renderMarkdown, setMarkdown: setMarkdown };

  document.querySelectorAll('.chat-wrap').forEach(function (wrap) {
    initChat(wrap);
  });
})();

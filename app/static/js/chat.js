/* chat.js — streaming chat client (agent detail + swarm sessions page).
 * Event protocol: state · turn.start · thinking · tool · tool_result · delta · done · delegate · error · heartbeat
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
    el.innerHTML = md(el.textContent);
    renderCode(el);
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

  function initChat(wrap) {
    if (!wrap || wrap.dataset.chatInit === '1') return;
    wrap.dataset.chatInit = '1';

    const sessionId = wrap.dataset.sessionId;
    const agentId = wrap.dataset.agentId;
    const userId = wrap.dataset.userId || 'web';
    const scroll = wrap.querySelector('.chat-scroll');
    const input = wrap.querySelector('.chat-input');
    const sendBtn = wrap.querySelector('.chat-send');
    const clearBtn = wrap.querySelector('.chat-clear');
    const statusEl = wrap.querySelector('.chat-status');
    const defaultAgentName = wrap.dataset.agentName || (wrap.querySelector('.chat-agent-name') || {}).textContent || 'Agent';

    if (!scroll || !input || !sendBtn || (!agentId && !sessionId)) return;

    let sending = false, es = null;

    function atBottom() { scroll.scrollTop = scroll.scrollHeight; }
    function setStatus(badge, label) {
      if (!statusEl) return;
      statusEl.className = 'badge ' + badge;
      statusEl.innerHTML = '<span class="pulse"></span>' + esc(label);
    }

    scroll.querySelectorAll('.prose').forEach(renderMarkdown);
    atBottom();

    function resize() {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }

    function closeStream() {
      if (es) { es.close(); es = null; }
      sending = false;
      sendBtn.disabled = !input.value.trim();
      input.focus();
    }

    function streamUrl(text) {
      if (sessionId) {
        return '/api/sessions/' + encodeURIComponent(sessionId) + '/chat/stream?user_id=' + encodeURIComponent(userId) + '&message=' + encodeURIComponent(text);
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/stream?user_id=' + encodeURIComponent(userId) + '&message=' + encodeURIComponent(text);
    }

    function clearUrl() {
      if (sessionId) {
        return '/api/sessions/' + encodeURIComponent(sessionId) + '/chat/clear';
      }
      return '/api/agents/' + encodeURIComponent(agentId) + '/chat/clear?user_id=' + encodeURIComponent(userId);
    }

    function streamTurn(text) {
      const turn = document.createElement('div');
      turn.className = 'turn';
      scroll.appendChild(turn);
      let thinkEl = null, asstEl = null, asstBody = null, raw = '', closed = false;
      let turnAgentName = defaultAgentName;
      let turnAgentId = agentId || '';

      es = new EventSource(streamUrl(text));

      function close() {
        if (closed) return;
        closed = true;
        closeStream();
      }

      es.addEventListener('state', function (e) {
        const d = JSON.parse(e.data || '{}');
        setStatus(d.busy ? 'amber' : 'ok', d.busy ? 'busy' : 'online');
      });
      es.addEventListener('delegate', function (e) {
        const d = JSON.parse(e.data || '{}');
        const row = document.createElement('div');
        row.className = 'delegate-line';
        row.textContent = d.content || ('Handing off to ' + (d.agent || d.agent_id));
        turn.appendChild(row);
        atBottom();
      });
      es.addEventListener('turn.start', function (e) {
        const d = JSON.parse(e.data || '{}');
        turnAgentName = d.agent || turnAgentName;
        turnAgentId = d.agent_id || turnAgentId;
        if (!thinkEl) { thinkEl = document.createElement('div'); thinkEl.className = 'thinking'; turn.appendChild(thinkEl); }
      });
      es.addEventListener('thinking', function (e) {
        const d = JSON.parse(e.data || '{}');
        if (d.agent) turnAgentName = d.agent;
        if (d.agent_id) turnAgentId = d.agent_id;
        if (!thinkEl) { thinkEl = document.createElement('div'); thinkEl.className = 'thinking'; turn.appendChild(thinkEl); }
        thinkEl.textContent += d.content || '';
        atBottom();
      });
      es.addEventListener('tool', function (e) {
        const d = JSON.parse(e.data || '{}');
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
        if (d.agent) turnAgentName = d.agent;
        if (d.agent_id) turnAgentId = d.agent_id;
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        if (!asstEl) {
          const tmp = document.createElement('div');
          tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
          asstEl = tmp.firstElementChild;
          turn.appendChild(asstEl);
          asstBody = asstEl.querySelector('.bubble-body');
        }
        raw += d.content || '';
        asstBody.innerHTML = md(raw);
        renderCode(asstBody);
        atBottom();
      });
      es.addEventListener('done', function (e) {
        const d = JSON.parse(e.data || '{}');
        if (d.agent) turnAgentName = d.agent;
        if (d.agent_id) turnAgentId = d.agent_id;
        if (thinkEl) { thinkEl.remove(); thinkEl = null; }
        if (d.content && asstBody) { raw = d.content; asstBody.innerHTML = md(raw); renderCode(asstBody); }
        if (!asstEl) {
          const tmp = document.createElement('div');
          tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
          asstEl = tmp.firstElementChild;
          turn.appendChild(asstEl);
          asstBody = asstEl.querySelector('.bubble-body');
          asstBody.innerHTML = md(d.content || '');
          renderCode(asstBody);
        }
        atBottom();
        setStatus('ok', 'online');
        close();
        Tomo.renderRail && Tomo.renderRail();
        wrap.dispatchEvent(new CustomEvent('tomo:chat-done'));
      });
      es.addEventListener('error', function () {
        if (closed) return;
        const tmp = document.createElement('div');
        tmp.innerHTML = bubbleHtml('assistant', turnAgentName, turnAgentId);
        const b = tmp.firstElementChild;
        turn.appendChild(b);
        b.querySelector('.bubble-body').innerHTML = '<span style="color:var(--danger)">Stream interrupted</span>';
        atBottom();
        close();
      });
      es.addEventListener('heartbeat', function () {});
      es.addEventListener('auth_expired', function () { window.location.href = '/login'; });
    }

    function doSend() {
      const text = input.value.trim();
      if (!text || sending) return;
      sending = true;
      sendBtn.disabled = true;
      input.value = '';
      resize();
      const empty = scroll.querySelector('.chat-empty');
      if (empty) empty.remove();
      const u = document.createElement('div');
      u.innerHTML = bubbleHtml('user', defaultAgentName);
      u.querySelector('.bubble-body').textContent = text;
      scroll.appendChild(u.firstElementChild);
      atBottom();
      streamTurn(text);
    }

    input.addEventListener('input', function () {
      sendBtn.disabled = !input.value.trim() || sending;
      resize();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
    });
    sendBtn.addEventListener('click', doSend);
    resize();

    if (clearBtn) {
      clearBtn.addEventListener('click', async function () {
        if (!confirm('Clear this conversation?')) return;
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

    return { destroy: closeStream };
  }

  window.TomoChat = { init: initChat, renderMarkdown: renderMarkdown };

  document.querySelectorAll('.chat-wrap').forEach(function (wrap) {
    initChat(wrap);
  });
})();

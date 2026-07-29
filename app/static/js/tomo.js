/* tomo.js — core utils: theme, toast, fetch, nav, helpers. No dependencies. */
(function () {
  "use strict";
  window.Tomo = window.Tomo || {};

  // ---- theme ----
  // tomo.js may load in <head> before #themeBtn exists; bind on DOM ready.
  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('tomo-theme', t);
    const moon = document.getElementById('iconMoon'), sun = document.getElementById('iconSun');
    if (moon && sun) {
      moon.style.display = t === 'dark' ? '' : 'none';
      sun.style.display = t === 'dark' ? 'none' : '';
    }
  }
  Tomo.applyTheme = applyTheme;

  function bindChrome() {
    applyTheme(localStorage.getItem('tomo-theme') || 'dark');
    const themeBtn = document.getElementById('themeBtn');
    if (themeBtn && !themeBtn.dataset.tomoBound) {
      themeBtn.dataset.tomoBound = '1';
      themeBtn.addEventListener('click', function () {
        const cur = document.documentElement.getAttribute('data-theme') || 'dark';
        applyTheme(cur === 'dark' ? 'light' : 'dark');
      });
    }
    const navToggle = document.getElementById('navToggle'), nav = document.getElementById('nav');
    if (navToggle && nav && !navToggle.dataset.tomoBound) {
      navToggle.dataset.tomoBound = '1';
      navToggle.addEventListener('click', function () { nav.classList.toggle('open'); });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindChrome);
  } else {
    bindChrome();
  }

  // ---- toast ----
  const ICONS = {
    ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
    err: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
  };
  function toast(msg, kind) {
    kind = kind || 'info';
    const box = document.getElementById('toasts');
    if (!box) return;
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = '<span class="ico">' + ICONS[kind] + '</span><span class="msg"></span>';
    el.querySelector('.msg').textContent = String(msg);
    box.appendChild(el);
    setTimeout(function () { el.classList.add('out'); setTimeout(function () { el.remove(); }, 220); }, 3200);
  }
  Tomo.toast = toast;

  // ---- fetch helper ----
  async function api(url, opts) {
    opts = opts || {};
    const res = await fetch(url, Object.assign({ headers: { 'Accept': 'application/json' }, credentials: 'same-origin' }, opts));
    if (res.status === 401) { window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname); return null; }
    const ct = res.headers.get('content-type') || '';
    if (ct.indexOf('json') !== -1) {
      const data = await res.json();
      if (!res.ok) throw Object.assign(new Error(data.detail || res.statusText), { status: res.status, body: data });
      return data;
    }
    if (!res.ok) throw new Error(res.statusText);
    return res;
  }
  Tomo.api = api;

  // ---- helpers ----
  Tomo.escapeHtml = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  Tomo.avatarHues = [200, 260, 330, 160, 30, 290, 80, 10];
  Tomo.avatarColor = function (id) {
    let h = 0; for (let i = 0; i < id.length; i++) { h = ((h << 5) - h) + id.charCodeAt(i); h |= 0; }
    return 'hsl(' + Tomo.avatarHues[Math.abs(h) % Tomo.avatarHues.length] + ',62%,42%)';
  };
  Tomo.truncate = function (s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n) + '…' : s; };
  Tomo.formatToolSummary = function (tool, args) {
    args = args || {};
    if (tool === 'bash' && args.command) return String(args.command);
    if (args.path) return String(args.path);
    if (args.query) return String(args.query);
    if (args.url) return String(args.url);
    var keys = Object.keys(args);
    if (keys.length === 1) return String(args[keys[0]]);
    if (!keys.length) return '';
    try { return JSON.stringify(args); } catch (_) { return ''; }
  };
  Tomo.toolResultPreview = function (text) {
    text = String(text == null ? '' : text);
    if (!text) return '';
    var lines = text.split('\n').length;
    if (lines > 1) return lines + ' lines';
    return Tomo.truncate(text.replace(/\s+/g, ' ').trim(), 48);
  };
  /** Return (or create) the timeline container inside an inspector body. */
  Tomo.siTimeline = function (body) {
    var tl = body.querySelector('.si-timeline');
    if (!tl) {
      var empty = body.querySelector('.si-empty');
      if (empty) empty.remove();
      tl = document.createElement('div');
      tl.className = 'si-timeline';
      body.appendChild(tl);
    }
    return tl;
  };

  /** Render one inspector timeline step. Returns the root element when useful. */
  Tomo.renderInspectorStep = function (body, kind, data) {
    var esc = Tomo.escapeHtml;
    var root = Tomo.siTimeline(body);
    var fmt = Tomo.formatToolSummary;
    var preview = Tomo.toolResultPreview;

    if (kind === 'thinking') {
      var text = String(data.content || '');
      var previewLine = text.split('\n')[0].trim();
      var wrap = document.createElement('div');
      wrap.className = 'si-item si-think';
      var think = document.createElement('details');
      think.className = 'si-card';
      think.innerHTML =
        '<summary class="si-card-hd">' +
          '<div class="si-hd-top">' +
            '<span class="si-tag think">Thought</span>' +
          '</div>' +
          (previewLine ? '<div class="si-hd-preview">' + esc(Tomo.truncate(previewLine, 120)) + '</div>' : '') +
        '</summary>' +
        '<div class="si-card-bd"><pre class="si-think-body"></pre></div>';
      think.querySelector('pre').textContent = text;
      wrap.innerHTML = '<span class="si-node" aria-hidden="true"></span>';
      wrap.appendChild(think);
      root.appendChild(wrap);
      return wrap;
    }

    if (kind === 'tool') {
      var toolName = data.tool || 'tool';
      var cmd = fmt(toolName, data.args || {});
      var card = document.createElement('div');
      card.className = 'si-item si-tool running';
      card.innerHTML =
        '<span class="si-node" aria-hidden="true"></span>' +
        '<div class="si-card">' +
          '<button type="button" class="si-card-hd">' +
            '<div class="si-hd-top">' +
              '<span class="si-tag tool">' + esc(toolName) + '</span>' +
              '<span class="si-hd-meta"></span>' +
            '</div>' +
            (cmd ? '<div class="si-hd-preview mono">' + esc(Tomo.truncate(cmd, 140)) + '</div>' : '') +
          '</button>' +
          '<div class="si-card-bd">' +
            (cmd ? '<div class="si-block"><span class="si-block-label">Command</span><pre class="si-cmd-full"></pre></div>' : '') +
            '<div class="si-block"><span class="si-block-label">Output</span><pre class="si-tres"></pre></div>' +
          '</div>' +
        '</div>';
      var cmdFull = card.querySelector('.si-cmd-full');
      if (cmdFull) cmdFull.textContent = cmd;
      card._res = card.querySelector('.si-tres');
      card._meta = card.querySelector('.si-hd-meta');
      card.querySelector('.si-card-hd').addEventListener('click', function () {
        card.classList.toggle('expanded');
      });
      root.appendChild(card);
      return card;
    }

    if (kind === 'tool_result') {
      var tools = body.querySelectorAll('.si-tool');
      var last = tools[tools.length - 1];
      if (!last || !last._res) return null;
      var resultText = typeof data.result === 'string' ? data.result : JSON.stringify(data.result || '');
      last._res.textContent = resultText;
      if (data.error) {
        last._res.classList.add('err');
        last.classList.add('is-error');
      }
      last.classList.add('has-output');
      last.classList.remove('running');
      if (last._meta) {
        var hint = preview(resultText);
        if (data.error) {
          var errLine = resultText.split('\n')[0].trim();
          hint = Tomo.truncate(errLine, 56) || 'Error';
          last._meta.classList.add('err');
        }
        last._meta.textContent = hint;
      }
      return last;
    }

    if (kind === 'delta' || kind === 'subagent_final' || kind === 'final') {
      var answerWrap = body.querySelector('.si-answer');
      if (!answerWrap) {
        answerWrap = document.createElement('div');
        answerWrap.className = 'si-item si-answer';
        var answer = document.createElement('details');
        answer.className = 'si-card';
        answer.open = true;
        answer.innerHTML =
          '<summary class="si-card-hd">' +
            '<div class="si-hd-top"><span class="si-tag answer">Answer</span></div>' +
            '<div class="si-hd-preview">Subagent response</div>' +
          '</summary>' +
          '<div class="si-card-bd"><div class="si-answer-body prose chat-prose"></div></div>';
        answerWrap.innerHTML = '<span class="si-node" aria-hidden="true"></span>';
        answerWrap.appendChild(answer);
        answerWrap._answerDetails = answer;
        answerWrap._raw = '';
        root.appendChild(answerWrap);
      }
      var bubble = answerWrap.querySelector('.si-answer-body');
      answerWrap._raw = (answerWrap._raw || '') + (data.content || '');
      if (window.TomoChat && TomoChat.setMarkdown) {
        TomoChat.setMarkdown(bubble, answerWrap._raw);
      } else {
        bubble.textContent = answerWrap._raw;
      }
      var prev = answerWrap.querySelector('.si-hd-preview');
      if (prev && answerWrap._raw.trim()) {
        prev.textContent = Tomo.truncate(answerWrap._raw.replace(/\s+/g, ' ').trim(), 100);
      }
      return answerWrap;
    }

    return null;
  };
  Tomo.ts = function (value) {
    const v = Number(value);
    if (!v) return '';
    const delta = (Date.now() / 1000) - v;
    if (delta < 60) return 'just now';
    if (delta < 3600) return Math.floor(delta / 60) + 'm ago';
    if (delta < 86400) return Math.floor(delta / 3600) + 'h ago';
    if (delta < 86400 * 7) return Math.floor(delta / 86400) + 'd ago';
    return new Date(v * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
})();

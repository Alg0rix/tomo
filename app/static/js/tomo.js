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

  var RAIL_KEY = 'tomo-app-rail';

  function railCollapsed() {
    return document.documentElement.classList.contains('is-rail-collapsed');
  }

  function setRailCollapsed(collapsed) {
    document.documentElement.classList.toggle('is-rail-collapsed', !!collapsed);
    document.documentElement.classList.remove('is-rail-open');
    var collapseBtn = document.getElementById('railCollapseBtn');
    var expandBtn = document.getElementById('railExpandBtn');
    if (expandBtn) expandBtn.classList.toggle('hidden', !collapsed);
    if (collapseBtn) {
      collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      collapseBtn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      collapseBtn.setAttribute('aria-label', collapseBtn.title);
    }
    if (expandBtn) {
      expandBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    }
    try {
      localStorage.setItem(RAIL_KEY, collapsed ? 'collapsed' : 'open');
    } catch (e) {}
  }

  function setRailOpen(open) {
    document.documentElement.classList.toggle('is-rail-open', !!open);
    var backdrop = document.getElementById('railBackdrop');
    if (backdrop) {
      if (open) backdrop.removeAttribute('hidden');
      else backdrop.setAttribute('hidden', '');
    }
  }

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

    // Sync expand button with anti-flash class from <head>.
    setRailCollapsed(railCollapsed());

    var collapseBtn = document.getElementById('railCollapseBtn');
    var expandBtn = document.getElementById('railExpandBtn');
    var mobileOpen = document.getElementById('railMobileOpen');
    var mobileClose = document.getElementById('navToggle');
    var backdrop = document.getElementById('railBackdrop');
    if (collapseBtn && !collapseBtn.dataset.tomoBound) {
      collapseBtn.dataset.tomoBound = '1';
      collapseBtn.addEventListener('click', function () {
        if (window.matchMedia('(max-width: 760px)').matches) {
          setRailOpen(false);
        } else {
          setRailCollapsed(!railCollapsed());
        }
      });
    }
    if (expandBtn && !expandBtn.dataset.tomoBound) {
      expandBtn.dataset.tomoBound = '1';
      expandBtn.addEventListener('click', function () { setRailCollapsed(false); });
    }
    if (mobileOpen && !mobileOpen.dataset.tomoBound) {
      mobileOpen.dataset.tomoBound = '1';
      mobileOpen.addEventListener('click', function () {
        setRailCollapsed(false);
        setRailOpen(true);
      });
    }
    if (mobileClose && !mobileClose.dataset.tomoBound) {
      mobileClose.dataset.tomoBound = '1';
      mobileClose.addEventListener('click', function () { setRailOpen(false); });
    }
    if (backdrop && !backdrop.dataset.tomoBound) {
      backdrop.dataset.tomoBound = '1';
      backdrop.addEventListener('click', function () { setRailOpen(false); });
    }
  }
  Tomo.setRailCollapsed = setRailCollapsed;
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

  /**
   * Force-instant scroll to bottom, bypassing CSS scroll-behavior: smooth.
   * Exported so other modules can use it without duplicating the pattern.
   */
  Tomo.scrollToBottomInstant = function (el) {
    if (!el) return;
    var prev = el.style.scrollBehavior;
    el.style.scrollBehavior = 'auto';
    el.scrollTop = el.scrollHeight;
    el.style.scrollBehavior = prev;
  };

  /**
   * Keep a scroll container pinned to the bottom while async layout (images,
   * mermaid, streaming turns, artifact panels) grows content.
   *
   * Cancel is gesture-first (wheel / touchmove / PageUp-style keys). Gap
   * checks on `scroll` only run outside a quiet window after programmatic
   * pins — layout thrash must not look like the user scrolled away.
   *
   * Idempotent: calling again on the same element replaces any prior stick.
   *
   * @param {Element} el  Scroll container
   * @param {object}  [opts]
   * @param {number}  [opts.userGap=80]     px gap that counts as scroll-away
   * @param {number[]} [opts.times=[50,200,500,1000,2000,4000]]  delayed go()
   * @param {number}  [opts.holdMs=20000]   auto-cleanup after this many ms
   */
  Tomo.stickScrollBottom = function (el, opts) {
    if (!el) return;
    opts = opts || {};
    var gap = opts.userGap != null ? opts.userGap : 80;
    var times = opts.times || [50, 200, 500, 1000, 2000, 4000];
    var holdMs = opts.holdMs != null ? opts.holdMs : 20000;
    var cancelled = false;
    var quietUntil = 0;
    var timers = [];
    var rafIds = [];
    var ro = null;
    var mo = null;
    var cleanupFn = null;

    if (el._tomoStickCleanup) {
      el._tomoStickCleanup();
    }

    function markProgrammatic() {
      // Ignore scroll events for a beat after we pin — covers residual
      // scroll events and overflow-anchor adjustments from our own jump.
      quietUntil = (typeof performance !== 'undefined' ? performance.now() : Date.now()) + 120;
    }

    function go() {
      if (cancelled) return;
      markProgrammatic();
      Tomo.scrollToBottomInstant(el);
    }
    el._tomoStickGo = go;

    function onUserGesture() {
      if (cancelled) return;
      cleanupFn();
    }

    function onScroll() {
      if (cancelled) return;
      var now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      if (now < quietUntil) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > gap) {
        cleanupFn();
      }
    }

    function onKeyNav(ev) {
      if (cancelled) return;
      var k = ev.key;
      if (k === 'PageUp' || k === 'Home' || k === 'ArrowUp') {
        cleanupFn();
      }
    }

    function onContentResize() {
      if (cancelled) return;
      go();
    }

    function bindImgEvents(node) {
      if (node.tagName !== 'IMG' || node.complete) return;
      node.addEventListener('load', go, { once: true });
      node.addEventListener('error', go, { once: true });
    }

    cleanupFn = function () {
      if (cancelled) return;
      cancelled = true;
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('wheel', onUserGesture);
      el.removeEventListener('touchmove', onUserGesture);
      el.removeEventListener('keydown', onKeyNav);
      timers.forEach(function (t) { clearTimeout(t); });
      timers = [];
      rafIds.forEach(function (id) { cancelAnimationFrame(id); });
      rafIds = [];
      if (ro) {
        ro.disconnect();
        ro = null;
      }
      if (mo) {
        mo.disconnect();
        mo = null;
      }
      if (el._tomoStickCleanup === cleanupFn) {
        el._tomoStickCleanup = null;
      }
      if (el._tomoStickGo === go) {
        el._tomoStickGo = null;
      }
    };

    go();
    rafIds.push(requestAnimationFrame(function () {
      go();
      rafIds.push(requestAnimationFrame(go));
    }));

    el.querySelectorAll('img').forEach(function (img) {
      bindImgEvents(img);
    });

    el.addEventListener('wheel', onUserGesture, { passive: true });
    el.addEventListener('touchmove', onUserGesture, { passive: true });
    el.addEventListener('scroll', onScroll, { passive: true });
    // Chat scroll is rarely focused; still catch PageUp when it is.
    el.addEventListener('keydown', onKeyNav);

    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(onContentResize);
      Array.prototype.forEach.call(el.children, function (child) {
        ro.observe(child);
      });
    }

    if (typeof MutationObserver !== 'undefined') {
      mo = new MutationObserver(function (mutations) {
        if (cancelled) return;
        for (var i = 0; i < mutations.length; i++) {
          var added = mutations[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var node = added[j];
            if (node.nodeType !== 1) continue;
            if (ro) ro.observe(node);
            if (node.tagName === 'IMG') {
              bindImgEvents(node);
            }
            node.querySelectorAll && node.querySelectorAll('img').forEach(function (img) {
              bindImgEvents(img);
            });
          }
        }
        go();
      });
      // childList only on direct children — hljs/mermaid span churn inside
      // bubbles must not re-pin on every token (that caused scroll thrash).
      mo.observe(el, { childList: true, subtree: false });
    }

    times.forEach(function (ms) { timers.push(setTimeout(go, ms)); });
    timers.push(setTimeout(cleanupFn, holdMs));

    el._tomoStickCleanup = cleanupFn;
  };

  /**
   * Re-pin if a stick is already active; otherwise start a short stick.
   * @param {Element} el  Scroll container
   * @param {object}  [opts]  start options when no stick is active
   */
  Tomo.nudgeScrollBottom = function (el, opts) {
    if (!el) return;
    if (typeof el._tomoStickGo === 'function') {
      el._tomoStickGo();
      return;
    }
    Tomo.stickScrollBottom(el, opts);
  };

  /** Line-level LCS ops for synthetic str_replace diffs. */
  Tomo._computeLineDiff = function (oldLines, newLines) {
    var m = oldLines.length, n = newLines.length;
    if (m * n > 80000) {
      return oldLines.map(function (l) { return { type: 'remove', line: l }; })
        .concat(newLines.map(function (l) { return { type: 'add', line: l }; }));
    }
    var dp = [];
    var i, j;
    for (i = 0; i <= m; i++) {
      dp[i] = new Int32Array(n + 1);
    }
    for (i = 1; i <= m; i++) {
      for (j = 1; j <= n; j++) {
        dp[i][j] = oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
    var ops = [];
    i = m; j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
        ops.push({ type: 'context', line: oldLines[i - 1] }); i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        ops.push({ type: 'add', line: newLines[j - 1] }); j--;
      } else {
        ops.push({ type: 'remove', line: oldLines[i - 1] }); i--;
      }
    }
    return ops.reverse();
  };

  Tomo.highlightDiffHtml = function (patch) {
    var esc = Tomo.escapeHtml;
    if (!patch) return '';
    return String(patch).split('\n').map(function (line) {
      if (line.indexOf('@@') === 0) return '<span class="hl-diff-header">' + esc(line) + '</span>';
      if (line.indexOf('--- ') === 0 || line.indexOf('+++ ') === 0) {
        return '<span class="hl-diff-filename">' + esc(line) + '</span>';
      }
      if (line.charAt(0) === '+') return '<span class="hl-diff-add">' + esc(line) + '</span>';
      if (line.charAt(0) === '-') return '<span class="hl-diff-remove">' + esc(line) + '</span>';
      if (line.charAt(0) === '\\') return '<span class="hl-diff-meta">' + esc(line) + '</span>';
      return '<span class="hl-diff-context">' + esc(line) + '</span>';
    }).join('\n');
  };

  /** Build highlighted unified-diff HTML from old/new strings. */
  Tomo.strReplaceDiffHtml = function (oldStr, newStr, filePath) {
    var esc = Tomo.escapeHtml;
    var oldLines = String(oldStr == null ? '' : oldStr).split('\n');
    var newLines = String(newStr == null ? '' : newStr).split('\n');
    var ops = Tomo._computeLineDiff(oldLines, newLines);
    var changed = [];
    for (var i = 0; i < ops.length; i++) {
      if (ops[i].type !== 'context') changed.push(i);
    }
    if (!changed.length) {
      return '<div class="diff-empty">No changes detected</div>';
    }
    var CTX = 3;
    var hunks = [];
    var hs = -1, he = -1, idx, lo, hi, k;
    for (k = 0; k < changed.length; k++) {
      idx = changed[k];
      lo = Math.max(0, idx - CTX);
      hi = Math.min(ops.length - 1, idx + CTX);
      if (hs === -1 || lo > he + 1) {
        if (hs !== -1) hunks.push([hs, he]);
        hs = lo; he = hi;
      } else {
        he = Math.max(he, hi);
      }
    }
    if (hs !== -1) hunks.push([hs, he]);

    var html = '';
    var adds = 0, dels = 0;
    if (filePath) {
      html += '<span class="hl-diff-filename">--- ' + esc(String(filePath)) + '</span>\n';
      html += '<span class="hl-diff-filename">+++ ' + esc(String(filePath)) + '</span>\n';
    }
    for (var h = 0; h < hunks.length; h++) {
      lo = hunks[h][0]; hi = hunks[h][1];
      var oldLn = 1, newLn = 1, oldC = 0, newC = 0;
      for (k = 0; k < lo; k++) {
        if (ops[k].type !== 'add') oldLn++;
        if (ops[k].type !== 'remove') newLn++;
      }
      for (k = lo; k <= hi; k++) {
        if (ops[k].type !== 'add') oldC++;
        if (ops[k].type !== 'remove') newC++;
      }
      html += '<span class="hl-diff-header">@@ -' + oldLn + ',' + oldC + ' +' + newLn + ',' + newC + ' @@</span>\n';
      for (k = lo; k <= hi; k++) {
        var type = ops[k].type, line = ops[k].line, e = esc(line);
        if (type === 'add') { html += '<span class="hl-diff-add">+' + e + '</span>\n'; adds++; }
        else if (type === 'remove') { html += '<span class="hl-diff-remove">-' + e + '</span>\n'; dels++; }
        else html += '<span class="hl-diff-context"> ' + e + '</span>\n';
      }
    }
    return { html: html, adds: adds, dels: dels };
  };

  /** Count +/− lines in a unified diff string. */
  Tomo.diffStat = function (patch) {
    var adds = 0, dels = 0;
    String(patch || '').split('\n').forEach(function (line) {
      if (line.charAt(0) === '+' && line.indexOf('+++') !== 0) adds++;
      else if (line.charAt(0) === '-' && line.indexOf('---') !== 0) dels++;
    });
    return { adds: adds, dels: dels };
  };

  /**
   * Present tool args for chat cards / inspector.
   * @returns {{ summary: string, detailHtml: string, isEdit: boolean, autoExpand: boolean }}
   */
  Tomo.presentToolArgs = function (tool, args) {
    args = args || {};
    var path = args.path || args.file_path || '';
    var oldKey = ('old_string' in args) ? 'old_string' : (('old_str' in args) ? 'old_str' : null);
    var newKey = ('new_string' in args) ? 'new_string' : (('new_str' in args) ? 'new_str' : null);

    // str_replace-style: show synthetic line diff (never when unified patch body present)
    if (oldKey && newKey && !(args.patch && tool === 'patch')) {
      var diff = Tomo.strReplaceDiffHtml(args[oldKey], args[newKey], path);
      var dhtml = typeof diff === 'string' ? diff : diff.html;
      var adds = typeof diff === 'object' ? diff.adds : 0;
      var dels = typeof diff === 'object' ? diff.dels : 0;
      var stat = (adds || dels) ? (' +' + adds + '/-' + dels) : '';
      var summary = (path || 'edit') + stat;
      if (args.count != null && args.count !== 1) summary += ' ×' + args.count;
      return {
        summary: summary,
        detailHtml: '<div class="diff-code-block">' + dhtml + '</div>',
        isEdit: true,
        autoExpand: true,
      };
    }

    if (tool === 'patch' && args.patch) {
      var st = Tomo.diffStat(args.patch);
      var psum = (path || 'patch') + ' +' + st.adds + '/-' + st.dels;
      var body = '';
      if (path) {
        body += '<div class="tool-meta-row"><span class="k">path</span><span class="v">' +
          Tomo.escapeHtml(String(path)) + '</span></div>';
      }
      body += '<div class="diff-code-block">' + Tomo.highlightDiffHtml(args.patch) + '</div>';
      return { summary: psum, detailHtml: body, isEdit: true, autoExpand: true };
    }

    if (tool === 'write_file' && args.content != null) {
      var lines = String(args.content).split('\n').length;
      var wsum = (path || 'write') + ' · ' + lines + ' lines';
      var wbody = '';
      if (path) {
        wbody += '<div class="tool-meta-row"><span class="k">path</span><span class="v">' +
          Tomo.escapeHtml(String(path)) + '</span></div>';
      }
      wbody += '<pre class="tool-code-block">' + Tomo.escapeHtml(String(args.content)) + '</pre>';
      return { summary: wsum, detailHtml: wbody, isEdit: true, autoExpand: false };
    }

    if (tool === 'todo') {
      var todosArg = args.todos;
      var merge = !!args.merge;
      var n = Array.isArray(todosArg) ? todosArg.length : null;
      var tsum = n == null ? 'read list' : (merge ? 'merge ' + n : 'plan ' + n);
      return {
        summary: tsum,
        detailHtml: '',
        isEdit: false,
        autoExpand: false,
      };
    }

    var sum = Tomo.formatToolSummary(tool, args);
    var json;
    try { json = JSON.stringify(args, null, 2); } catch (_) { json = String(args); }
    return {
      summary: sum,
      detailHtml: '<pre class="tool-code-block">' + Tomo.escapeHtml(json) + '</pre>',
      isEdit: false,
      autoExpand: false,
    };
  };

  Tomo.formatToolSummary = function (tool, args) {
    args = args || {};
    if (tool === 'bash' && args.command) return String(args.command);
    if (tool === 'patch' && (args.path || args.patch)) {
      var st = Tomo.diffStat(args.patch || '');
      return (args.path || 'patch') + (args.patch ? (' +' + st.adds + '/-' + st.dels) : '');
    }
    if (tool === 'str_replace' && args.path) {
      return String(args.path);
    }
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

  /**
   * Compact tool row: status · name · summary · chip · expand.
   * @param {{tool?: string, args?: object}|string} toolOrData
   * @param {object} [args]
   * @param {{running?: boolean}} [opts]
   */
  Tomo.buildToolCard = function (toolOrData, args, opts) {
    var tool, presented;
    opts = opts || {};
    if (typeof toolOrData === 'string') {
      tool = toolOrData;
      args = args || {};
    } else {
      tool = (toolOrData && toolOrData.tool) || 'tool';
      args = (toolOrData && toolOrData.args) || {};
      if (toolOrData && toolOrData.running != null && opts.running == null) {
        opts.running = toolOrData.running;
      }
    }
    presented = Tomo.presentToolArgs(tool, args);
    var running = !!opts.running;
    var expanded = !!presented.autoExpand;
    var card = document.createElement('div');
    card.className = 'tool' +
      (running ? ' loading' : ' ok') +
      (presented.isEdit ? ' is-edit' : '') +
      (expanded ? ' expanded' : '');
    var callId = (opts.call_id || opts.callId ||
      (toolOrData && toolOrData.call_id) || (toolOrData && toolOrData.callId) || '').toString();
    if (callId) card.dataset.callId = callId;
    card.dataset.toolName = tool;
    var summary = presented.summary || '';
    card.innerHTML =
      '<button type="button" class="tool-head" aria-expanded="' + (expanded ? 'true' : 'false') + '">' +
        '<span class="tstatus" aria-hidden="true"></span>' +
        '<span class="tname">' + Tomo.escapeHtml(tool) + '</span>' +
        '<span class="targs">' + Tomo.escapeHtml(summary) + '</span>' +
        '<span class="tchip"></span>' +
        '<span class="chevron" aria-hidden="true"></span>' +
      '</button>' +
      '<div class="tool-body">' +
        (presented.detailHtml
          ? '<div class="tdetail"><span class="tool-sec-label">Input</span>' + presented.detailHtml + '</div>'
          : '') +
        '<div class="tres-wrap"><span class="tool-sec-label">Output</span><pre class="tres"></pre></div>' +
      '</div>';
    card._res = card.querySelector('.tres');
    card._chip = card.querySelector('.tchip');
    card._head = card.querySelector('.tool-head');
    Tomo.wireToolCard(card);
    return card;
  };

  Tomo.wireToolCard = function (card) {
    if (!card || card.dataset.toolWired === '1') return card;
    card.dataset.toolWired = '1';
    var head = card.querySelector('.tool-head');
    if (!head) return card;
    head.addEventListener('click', function (e) {
      if (e.target.closest('.diff-code-block') || e.target.closest('.tool-code-block')) return;
      card.classList.toggle('expanded');
      head.setAttribute('aria-expanded', card.classList.contains('expanded') ? 'true' : 'false');
    });
    return card;
  };

  Tomo.todoGlyph = function (status) {
    if (status === 'completed') return '[x]';
    if (status === 'in_progress') return '[>]';
    if (status === 'cancelled') return '[-]';
    return '[ ]';
  };

  /**
   * Session-scoped todo dock — lives in chat chrome (above composer), not
   * inside the scrolling message thread.
   */
  Tomo.ensureTodoDock = function (fromEl) {
    var wrap = null;
    if (fromEl && fromEl.nodeType === 1) {
      wrap = fromEl.closest('.chat-wrap');
    }
    if (!wrap) wrap = document.querySelector('.chat-wrap[data-session-id], .chat-wrap');
    if (!wrap) return null;
    var dock = wrap.querySelector('.chat-todo-dock');
    if (dock) return dock;
    var main = wrap.querySelector('.chat-main') || wrap;
    dock = document.createElement('aside');
    dock.className = 'chat-todo-dock';
    dock.hidden = true;
    dock.setAttribute('aria-label', 'Session todo list');
    var composer = main.querySelector('.composer');
    if (composer) main.insertBefore(dock, composer);
    else main.appendChild(dock);
    return dock;
  };

  Tomo.clearTodoDock = function (fromEl) {
    var dock = Tomo.ensureTodoDock(fromEl);
    if (!dock) return;
    dock.hidden = true;
    dock._todos = [];
    var panel = dock.querySelector('.todo-panel');
    if (panel) panel.remove();
  };

  /**
   * Upsert the session Todo checklist into the chrome dock (not the thread).
   * ``parent`` is any element inside the chat wrap (used to locate the dock).
   */
  Tomo.upsertTodoPanel = function (parent, todos) {
    var dock = Tomo.ensureTodoDock(parent);
    if (!dock) return null;
    if (!Array.isArray(todos) || !todos.length) {
      Tomo.clearTodoDock(parent);
      return null;
    }
    dock.hidden = false;
    var panel = dock.querySelector(':scope > .todo-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'todo-panel';
      dock.appendChild(panel);
      panel.addEventListener('click', function (ev) {
        var btn = ev.target.closest('.todo-hd');
        if (!btn || !panel.contains(btn)) return;
        panel.classList.toggle('collapsed');
        Tomo.renderTodoPanel(panel);
      });
    }
    panel._todos = todos.slice();
    Tomo.renderTodoPanel(panel);
    return panel;
  };

  Tomo.renderTodoPanel = function (panel) {
    if (!panel || !panel._todos) return;
    var esc = Tomo.escapeHtml;
    var todos = panel._todos;
    var done = todos.filter(function (t) { return t && t.status === 'completed'; }).length;
    var collapsed = panel.classList.contains('collapsed');
    var rows = todos.map(function (t) {
      var st = (t && t.status) || 'pending';
      var content = (t && t.content) || '';
      return (
        '<div class="todo-row status-' + esc(st) + '">' +
          '<span class="todo-glyph" aria-hidden="true">' + esc(Tomo.todoGlyph(st)) + '</span>' +
          '<span class="todo-text">' + esc(content) + '</span>' +
        '</div>'
      );
    }).join('');
    panel.innerHTML =
      '<button type="button" class="todo-hd" aria-expanded="' + (!collapsed) + '">' +
        '<span class="todo-caret">' + (collapsed ? '▸' : '▾') + '</span> ' +
        '<span class="todo-title">Todo</span> ' +
        '<span class="todo-count">(' + done + '/' + todos.length + ')</span>' +
      '</button>' +
      (collapsed ? '' : '<div class="todo-bd">' + rows + '</div>');
  };

  /**
   * Find the tool card to finish for a tool_result event.
   * Prefer call_id match; else first still-loading card with same tool name;
   * else first still-loading card. Avoids parallel-tool "always last card" bugs.
   */
  Tomo.findToolCard = function (root, data) {
    if (!root) return null;
    data = data || {};
    var callId = (data.call_id || data.callId || '').toString();
    // ATG waves may only carry atg_node; treat as call id when present.
    if (!callId && data.atg_node) callId = 'atg:' + String(data.atg_node);
    var toolName = (data.tool || data.name || data.function || '').toString();
    var cards = root.querySelectorAll('.tool, .si-tool');
    var i, card, cId, cName, loading;
    if (callId) {
      for (i = 0; i < cards.length; i++) {
        card = cards[i];
        cId = (card.dataset && card.dataset.callId) || card.getAttribute('data-call-id') || '';
        if (cId === callId) return card;
      }
    }
    var firstLoading = null;
    var firstLoadingName = null;
    for (i = 0; i < cards.length; i++) {
      card = cards[i];
      loading = card.classList.contains('loading') || card.classList.contains('running');
      if (!loading) continue;
      if (!firstLoading) firstLoading = card;
      cName = (card.dataset && card.dataset.toolName) || '';
      if (!cName) {
        var nameEl = card.querySelector('.tname, .si-tag.tool');
        cName = nameEl ? (nameEl.textContent || '').trim() : '';
      }
      if (toolName && cName === toolName && !firstLoadingName) firstLoadingName = card;
    }
    return firstLoadingName || firstLoading || null;
  };

  /** Attach tool output to a card and flip status to ok/error. */
  Tomo.finishToolCard = function (card, result, isError) {
    if (!card) return;
    var resultText = typeof result === 'string' ? result : JSON.stringify(result == null ? '' : result);
    if (card._res) card._res.textContent = resultText;
    card.classList.remove('loading');
    card.classList.remove('running');
    card.classList.toggle('error', !!isError);
    card.classList.toggle('ok', !isError);
    card.classList.add('has-output');
    if (card._chip) {
      var hint = isError
        ? (Tomo.truncate((resultText.split('\n')[0] || 'Error').trim(), 56) || 'Error')
        : Tomo.toolResultPreview(resultText);
      card._chip.textContent = hint;
      card._chip.classList.toggle('err', !!isError);
    }
    if (isError || card.classList.contains('is-edit')) {
      card.classList.add('expanded');
      if (card._head) card._head.setAttribute('aria-expanded', 'true');
    }
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
      var presented = Tomo.presentToolArgs(toolName, data.args || {});
      var cmd = presented.summary || fmt(toolName, data.args || {});
      var card = document.createElement('div');
      card.className = 'si-item si-tool running' + (presented.autoExpand ? ' expanded' : '');
      if (data.call_id) card.dataset.callId = data.call_id;
      card.dataset.toolName = toolName;
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
            (presented.detailHtml
              ? '<div class="si-block"><span class="si-block-label">Changes</span><div class="si-tool-detail"></div></div>'
              : '') +
            '<div class="si-block"><span class="si-block-label">Output</span><pre class="si-tres"></pre></div>' +
          '</div>' +
        '</div>';
      var detailEl = card.querySelector('.si-tool-detail');
      if (detailEl && presented.detailHtml) detailEl.innerHTML = presented.detailHtml;
      card._res = card.querySelector('.si-tres');
      card._meta = card.querySelector('.si-hd-meta');
      card.querySelector('.si-card-hd').addEventListener('click', function () {
        card.classList.toggle('expanded');
      });
      root.appendChild(card);
      return card;
    }

    if (kind === 'tool_result') {
      var last = Tomo.findToolCard(body, data) || Tomo.findToolCard(root, data);
      if (!last || !last._res) {
        if (Array.isArray(data.todos)) Tomo.upsertTodoPanel(root, data.todos);
        return null;
      }
      var resultText = typeof data.result === 'string' ? data.result : JSON.stringify(data.result || '');
      last._res.textContent = resultText;
      if (data.error) {
        last._res.classList.add('err');
        last.classList.add('is-error');
      }
      last.classList.add('has-output');
      last.classList.remove('running');
      last.classList.remove('loading');
      if (last._meta) {
        var hint = preview(resultText);
        if (data.error) {
          var errLine = resultText.split('\n')[0].trim();
          hint = Tomo.truncate(errLine, 56) || 'Error';
          last._meta.classList.add('err');
        }
        last._meta.textContent = hint;
      }
      if (Array.isArray(data.todos)) Tomo.upsertTodoPanel(root, data.todos);
      return last;
    }

    if (kind === 'todos') {
      return Tomo.upsertTodoPanel(root, data.todos || []);
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

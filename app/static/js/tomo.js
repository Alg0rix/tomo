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
    var trigger = document.getElementById('railMobileOpen');
    if (trigger) trigger.setAttribute('aria-expanded', String(!!open));
    var rail = document.getElementById('appRail');
    if (rail && window.matchMedia('(max-width: 760px)').matches) rail.inert = !open;
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

    var more = document.getElementById('railMore');
    var moreBtn = document.getElementById('railMoreBtn');
    var moreMenu = document.getElementById('railMoreMenu');
    if (more && moreBtn && moreMenu && !moreBtn.dataset.tomoBound) {
      moreBtn.dataset.tomoBound = '1';
      function setMoreOpen(open) {
        more.classList.toggle('is-open', !!open);
        moreBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) moreMenu.removeAttribute('hidden');
        else moreMenu.setAttribute('hidden', '');
      }
      moreBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        setMoreOpen(moreMenu.hasAttribute('hidden'));
      });
      document.addEventListener('click', function (e) {
        if (!more.contains(e.target)) setMoreOpen(false);
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') setMoreOpen(false);
      });
    }
  }
  Tomo.setRailCollapsed = setRailCollapsed;
  Tomo.setRailOpen = setRailOpen;

  // ---- rail badges: needs on Home, running dot on Chat ----
  var badgeTimer = null;
  function setRailBadges(data) {
    document.querySelectorAll('[data-rail-badge]').forEach(function (el) {
      if (!data || !(el.dataset.railBadge in data)) return;
      var n = Number(data[el.dataset.railBadge]) || 0;
      if (el.classList.contains('app-rail-badge')) el.textContent = n > 99 ? '99+' : String(n);
      el.hidden = n <= 0;
    });
  }
  Tomo.setRailBadges = setRailBadges;
  function pollRailBadges() {
    clearTimeout(badgeTimer);
    if (!document.querySelector('[data-rail-badge]')) return;
    if (document.visibilityState === 'visible') {
      fetch('/api/home/badges', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) setRailBadges(d); })
        .catch(function () {});
    }
    badgeTimer = setTimeout(pollRailBadges, 15000);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') pollRailBadges();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bindChrome(); pollRailBadges(); });
  } else {
    bindChrome();
    pollRailBadges();
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
    if (!el || el._tomoFollowPaused) return;
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
    if (!el || el._tomoFollowPaused) return;
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
    if (!el || el._tomoFollowPaused) return;
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
    if (keys.length === 1 && typeof args[keys[0]] !== 'object') return String(args[keys[0]]);
    if (!keys.length) return '';
    // Several args: compact "k=v" pairs, primitives only, short values.
    var pairs = keys.filter(function (k) {
      var v = args[k];
      return v != null && v !== '' && typeof v !== 'object';
    }).map(function (k) {
      return k + '=' + Tomo.truncate(String(args[k]).replace(/\s+/g, ' '), 40);
    });
    if (pairs.length) return pairs.join(' ');
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
   * Turn a raw tool error into one readable line.
   * "Error: [TAB_NOT_FOUND] Tab not found" -> {message: "Tab not found", code: "TAB_NOT_FOUND"}
   */
  Tomo.humanizeToolError = function (text) {
    var line = String(text == null ? '' : text).split('\n').map(function (l) { return l.trim(); })
      .filter(Boolean)[0] || 'Failed';
    line = line.replace(/^(error|exception)\s*:\s*/i, '');
    var code = '';
    var m = line.match(/^\[([A-Z0-9_]{3,})\]\s*(.*)$/);
    if (m) { code = m[1]; line = m[2] || m[1]; }
    line = line.replace(/^tool '[^']+' failed:\s*/i, '');
    return { message: Tomo.truncate(line, 140), code: code };
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
    var discovery = tool === 'search_tools';
    var displayName = discovery ? (running ? 'Finding relevant tools…' : 'Tool discovery') : tool;
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
    var summary = discovery ? '' : (presented.summary || '');
    card.innerHTML =
      '<button type="button" class="tool-head" aria-expanded="' + (expanded ? 'true' : 'false') + '"' +
        (summary ? ' title="' + Tomo.escapeHtml(tool + ' ' + summary) + '"' : '') + '>' +
        '<span class="tstatus" aria-hidden="true"></span>' +
        '<span class="tname"' + (discovery ? ' role="status" aria-live="polite"' : '') + '>' + Tomo.escapeHtml(displayName) + '</span>' +
        '<span class="targs">' + Tomo.escapeHtml(Tomo.truncate(summary, 160)) + '</span>' +
        '<span class="tchip"></span>' +
        '<span class="chevron" aria-hidden="true"></span>' +
      '</button>' +
      '<div class="tool-err" hidden></div>' +
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

  /** Friendly discovery status; schema accounting stays inside the fold. */
  function finishDiscoveryCard(card, resultText, isError) {
    var name = card.querySelector('.tname');
    var previous = card.querySelector('.tool-discovery-details');
    if (previous) previous.remove();
    var data = null;
    if (!isError) {
      try { data = JSON.parse(resultText); } catch (_) {}
    }
    var loaded = data && Array.isArray(data.loaded_tools)
      ? Array.from(new Set(data.loaded_tools.filter(function (n) { return typeof n === 'string' && n; })))
      : null;
    var label = isError ? 'Tool discovery failed' : 'Tool discovery completed';
    if (loaded) {
      label = loaded.length
        ? 'Loaded ' + loaded.length + (loaded.length === 1 ? ' tool' : ' tools')
        : (data.total_matches > 0 ? 'No tools loaded' : 'No matching tools');
    }
    if (name) name.textContent = label;
    if (card._head) card._head.title = label;
    if (!loaded) return;
    var detail = document.createElement('div');
    detail.className = 'tdetail tool-discovery-details';
    var toolsLabel = document.createElement('span');
    toolsLabel.className = 'tool-sec-label';
    toolsLabel.textContent = 'Loaded tools';
    var tools = document.createElement('div');
    tools.textContent = loaded.length ? loaded.join(', ') : 'None';
    detail.append(toolsLabel, tools);
    if (Number.isFinite(data.schema_tokens) && data.schema_tokens >= 0) {
      var usageLabel = document.createElement('span');
      usageLabel.className = 'tool-sec-label';
      usageLabel.textContent = 'Schema context';
      var usage = document.createElement('div');
      var targetKnown = Number.isFinite(data.schema_target_tokens) && data.schema_target_tokens > 0;
      usage.textContent = 'About ' + data.schema_tokens.toLocaleString() + ' tokens' +
        (targetKnown ? ' · ' + data.schema_target_tokens.toLocaleString() + '-token soft target' : ' · Target unavailable');
      if (targetKnown && data.over_target === true) usage.textContent += '. Above target; tools remain available.';
      detail.append(usageLabel, usage);
    }
    var body = card.querySelector('.tool-body');
    if (body) body.insertBefore(detail, body.querySelector('.tres-wrap'));
  }

  Tomo.wireToolCard = function (card) {
    if (!card || card.dataset.toolWired === '1') return card;
    card.dataset.toolWired = '1';
    var head = card.querySelector('.tool-head');
    if (!head) return card;
    head.addEventListener('click', function (e) {
      if (e.target.closest('.diff-code-block') || e.target.closest('.tool-code-block')) return;
      card._userToggled = true;
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
    // Lives inside the composer so the plan strip stays glued to the input,
    // whatever height the textarea grows to.
    var composer = main.querySelector('.composer');
    var shell = composer && composer.querySelector('.composer-shell');
    if (shell) composer.insertBefore(dock, shell);
    else if (composer) main.insertBefore(dock, composer);
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

  /** Parse ``{todos:[...]}`` from a todo tool JSON result string. */
  Tomo.parseTodosResult = function (text) {
    if (!text || typeof text !== 'string') return null;
    var s = text.trim();
    if (s.charAt(0) !== '{') return null;
    try {
      var data = JSON.parse(s);
      if (data && Array.isArray(data.todos)) return data.todos;
    } catch (_) {}
    return null;
  };

  /**
   * Restore the todo dock from the latest ``todo`` tool_output in history.
   * Used when the pending API is empty or as a first paint before rehydrate.
   */
  Tomo.restoreTodosFromHistory = function (fromEl, entries) {
    if (!Array.isArray(entries) || !entries.length) return null;
    for (var i = entries.length - 1; i >= 0; i--) {
      var e = entries[i];
      if (!e || e.type !== 'tool_output' || e.error) continue;
      var fn = (e.function || e.tool || '').toString();
      if (fn !== 'todo') continue;
      var todos = Tomo.parseTodosResult(e.content || '');
      if (todos && todos.length) {
        return Tomo.upsertTodoPanel(fromEl, todos);
      }
    }
    return null;
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
      // Plan strip starts folded: one line with progress + current step.
      panel.className = 'todo-panel collapsed';
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
    var current = todos.filter(function (t) { return t && t.status === 'in_progress'; })[0] ||
      todos.filter(function (t) { return t && (t.status || 'pending') === 'pending'; })[0] || null;
    var next = null;
    if (current) {
      var ci = todos.indexOf(current);
      next = todos.slice(ci + 1).filter(function (t) { return t && (t.status || 'pending') === 'pending'; })[0] || null;
    }
    var pips = todos.map(function (t) {
      var st = (t && t.status) || 'pending';
      return '<i class="pip ' + esc(st) + '"></i>';
    }).join('');
    var headline = current
      ? '<b>' + esc(current.content || '') + '</b>' + (next ? ' <span class="todo-next">· then ' + esc(next.content || '') + '</span>' : '')
      : '<b>All done</b>';
    var rows = todos.map(function (t) {
      var st = (t && t.status) || 'pending';
      return '<li class="todo-row status-' + esc(st) + '">' + esc((t && t.content) || '') + '</li>';
    }).join('');
    panel.classList.toggle('is-complete', !current);
    panel.innerHTML =
      '<button type="button" class="todo-hd" aria-expanded="' + (!collapsed) + '" title="Plan">' +
        '<span class="todo-count">' + done + '/' + todos.length + '</span>' +
        '<span class="todo-pips" aria-hidden="true">' + pips + '</span>' +
        '<span class="todo-cur">' + headline + '</span>' +
        '<span class="todo-caret" aria-hidden="true"></span>' +
      '</button>' +
      (collapsed ? '' : '<ol class="todo-bd">' + rows + '</ol>');
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

  var LIVE_OUTPUT_MAX = 200000;

  /** Append a live output chunk (bash stream) to a still-running tool card. */
  Tomo.appendToolOutput = function (card, chunk) {
    if (!card || !card._res || !chunk) return;
    if (!card.classList.contains('loading') && !card.classList.contains('running')) return;
    var live = (card._live || '') + String(chunk);
    if (live.length > LIVE_OUTPUT_MAX) live = live.slice(live.length - LIVE_OUTPUT_MAX);
    card._live = live;
    var pre = card._res;
    var pinned = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
    // Carriage returns redraw the current line (progress bars), like a terminal.
    pre.textContent = live.replace(/\r\n/g, '\n').split('\n').map(function (line) {
      return line.slice(line.lastIndexOf('\r') + 1);
    }).join('\n');
    if (!card.classList.contains('expanded') && !card._userToggled) {
      card.classList.add('expanded');
      card._liveExpanded = true;
      if (card._head) card._head.setAttribute('aria-expanded', 'true');
    }
    if (pinned) pre.scrollTop = pre.scrollHeight;
  };

  /** Attach tool output to a card and flip status to ok/error. */
  Tomo.finishToolCard = function (card, result, isError) {
    if (!card) return;
    if (card._liveExpanded && !card._userToggled) {
      card.classList.remove('expanded');
      if (card._head) card._head.setAttribute('aria-expanded', 'false');
    }
    card._live = null;
    card._liveExpanded = false;
    var resultText = typeof result === 'string' ? result : JSON.stringify(result == null ? '' : result);
    if (card._res) card._res.textContent = resultText;
    card.classList.remove('loading');
    card.classList.remove('running');
    card.classList.toggle('error', !!isError);
    card.classList.toggle('ok', !isError);
    card.classList.add('has-output');
    if (card._chip) {
      var quiet = isError || card.dataset.toolName === 'todo' || card.dataset.toolName === 'search_tools';
      card._chip.textContent = quiet ? '' : Tomo.toolResultPreview(resultText);
      card._chip.classList.toggle('err', !!isError);
    }
    var errEl = card.querySelector(':scope > .tool-err');
    if (errEl) {
      if (isError) {
        var he = Tomo.humanizeToolError(resultText);
        errEl.innerHTML = Tomo.escapeHtml(he.message) +
          (he.code ? ' <span class="tool-err-code">' + Tomo.escapeHtml(he.code) + '</span>' : '');
        errEl.hidden = false;
      } else {
        errEl.hidden = true;
      }
    }
    if (card.classList.contains('is-edit')) {
      card.classList.add('expanded');
      if (card._head) card._head.setAttribute('aria-expanded', 'true');
    }
    if (card.dataset.toolName === 'search_tools') finishDiscoveryCard(card, resultText, !!isError);
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
  Tomo.buildReasoningCard = function (content) {
    var text = String(content || '').trim();
    var note = document.createElement('div');
    // si-think stays: stream resume counts rendered reasoning by this class.
    note.className = 'wn-note si-think';
    var body = document.createElement('div');
    body.className = 'wn-note-text prose chat-prose';
    if (window.TomoChat && TomoChat.setMarkdown) TomoChat.setMarkdown(body, text);
    else body.textContent = text;
    note.appendChild(body);
    // Long notes clamp to a few lines; click to read the rest.
    if (text.length > 280 || text.split('\n').length > 4) {
      note.classList.add('is-clamped');
      note.addEventListener('click', function (ev) {
        if (ev.target.closest('a, button, pre, code')) return;
        note.classList.toggle('is-clamped');
      });
    }
    return note;
  };

  /** Refresh a reasoning note while its text is still streaming in. */
  Tomo.updateReasoningCard = function (note, content) {
    if (!note) return;
    var body = note.querySelector('.wn-note-text') || note.querySelector('pre');
    if (!body) return;
    note.classList.add('is-streaming');
    note._pendingText = String(content || '');
    if (!(window.TomoMarkdown && TomoMarkdown.renderInto)) {
      body.textContent = note._pendingText;
      return;
    }
    // Render markdown live — partial mode closes a half-streamed "**" so bold
    // headings don't flash as raw asterisks — but coalesce delta bursts.
    if (note._mdTimer) return;
    note._mdTimer = setTimeout(function () {
      note._mdTimer = null;
      if (!note.isConnected) return;
      TomoMarkdown.renderInto(body, note._pendingText, { partial: true });
    }, 80);
  };

  // ── Subagent lanes (delegate / swarm rows) ─────────────────────────
  // One shared row shape for live, resume and history renders. The row shows
  // who is working, on what, and the latest step — not a fake progress bar.

  function firstSentence(text, n) {
    // Skip markdown tables / rules so previews read as prose.
    var lines = String(text || '').split('\n').map(function (l) { return l.trim(); }).filter(function (l) {
      return l && l.charAt(0) !== '|' && !/^[-=*_]{3,}$/.test(l);
    });
    var t = (lines[0] || '').replace(/[#*_`>]/g, '').replace(/\s+/g, ' ').trim();
    var m = t.match(/^(.{12,}?[.!?])(\s|$)/);
    return Tomo.truncate(m ? m[1] : t, n || 120);
  }

  Tomo.buildSwarmRow = function (o) {
    o = o || {};
    var esc = Tomo.escapeHtml;
    var aid = o.aid || '';
    var name = o.name || aid || 'Agent';
    var row = document.createElement('div');
    row.className = 'swarm-row active';
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.dataset.agentId = aid;
    row.dataset.instanceKey = o.key || aid;
    if (!o.historic) row.dataset.start = String(Date.now());
    row._stats = { tools: 0, errors: 0, steps: 0 };
    row.style.setProperty('--c', Tomo.avatarColor ? Tomo.avatarColor(aid || name) : 'var(--accent)');
    var lane = (o.total || 1) > 1 ? '<span class="sw-lane">' + (o.idx || 1) + '/' + o.total + '</span>' : '';
    row.innerHTML =
      '<span class="sw-av" aria-hidden="true"><span>' + esc(name.charAt(0).toUpperCase()) + '</span></span>' +
      '<div class="sw-body">' +
        '<div class="sw-head">' +
          '<span class="sw-dot" aria-hidden="true"></span>' +
          '<span class="name">' + esc(name) + '</span>' + lane +
          '<span class="sw-role"></span>' +
          '<span class="sw-state">' + (o.historic ? '' : 'starting') + '</span>' +
          '<span class="sw-open" aria-hidden="true">Trace</span>' +
        '</div>' +
        (o.task ? '<div class="task">' + esc(o.task) + '</div>' : '') +
        '<div class="sw-deps" hidden></div>' +
        '<div class="sw-live" aria-live="polite"></div>' +
      '</div>' +
      '<div class="sw-track" aria-hidden="true"><i></i></div>' +
      // Kept for older callers that still poke its width; hidden by CSS.
      '<div class="swarm-progress" hidden><div class="swarm-progress-bar"></div></div>';
    row.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); row.click(); }
    });
    return row;
  };

  function swarmStateText(row) {
    var st = row._stats || { tools: 0, errors: 0 };
    var parts = [];
    if (st.tools) parts.push(st.tools + (st.tools === 1 ? ' tool' : ' tools'));
    if (st.errors) parts.push(st.errors + (st.errors === 1 ? ' error' : ' errors'));
    return parts.join(' · ');
  }

  Tomo.swarmRowEvent = function (row, kind, data) {
    if (!row || !row.querySelector) return;
    data = data || {};
    var st = row._stats || (row._stats = { tools: 0, errors: 0, steps: 0 });
    var live = row.querySelector('.sw-live');
    var set = function (cls, html) {
      if (!live) return;
      live.className = 'sw-live ' + cls;
      live.innerHTML = html;
    };
    var esc = Tomo.escapeHtml;
    if (kind === 'thinking' && data.content) {
      st.steps++;
      set('is-note', esc(firstSentence(data.content, 140)));
    } else if (kind === 'tool' && data.tool === 'swarm_board') {
      var act = (data.args || {}).action;
      set('is-note', act === 'read' ? 'reading the shared board' : act === 'send' ? 'messaging another agent' : 'posting to the shared board');
    } else if (kind === 'tool') {
      st.tools++;
      var sum = Tomo.formatToolSummary(data.tool || '', data.args || {});
      set('is-tool', '<span class="mono">' + esc(data.tool || 'tool') + '</span> ' +
        '<span class="sw-arg">' + esc(Tomo.truncate(sum, 90)) + '</span>');
    } else if (kind === 'tool_result' && data.error) {
      st.errors++;
      var he = Tomo.humanizeToolError(typeof data.result === 'string' ? data.result : '');
      set('is-err', esc(he.message));
    } else if ((kind === 'subagent_final' || kind === 'final') && data.content) {
      row._answer = (row._answer || '') + data.content;
      set('is-answer', esc(firstSentence(row._answer, 180)));
    } else if (kind === 'delta' && data.content) {
      row._answer = (row._answer || '') + data.content;
      set('is-answer', esc(firstSentence(row._answer, 180)));
    }
    if (row.classList.contains('active')) {
      var state = row.querySelector('.sw-state');
      if (state) state.dataset.counts = swarmStateText(row);
    }
  };

  Tomo.swarmRowDone = function (row, status) {
    if (!row || !row.classList) return;
    var failed = status === 'error';
    row.classList.remove('active');
    row.classList.remove('done', 'error');
    row.classList.add(failed ? 'error' : 'done');
    var state = row.querySelector('.sw-state');
    if (state) {
      var counts = swarmStateText(row);
      state.textContent = (failed ? 'failed' : 'done') + (counts ? ' · ' + counts : '');
      delete state.dataset.counts;
    }
  };

  // Elapsed clock for running lanes (one timer for the whole page).
  setInterval(function () {
    var rows = document.querySelectorAll('.swarm-row.active[data-start]');
    for (var i = 0; i < rows.length; i++) {
      var state = rows[i].querySelector('.sw-state');
      if (!state) continue;
      var secs = Math.max(0, Math.round((Date.now() - Number(rows[i].dataset.start)) / 1000));
      var clock = Math.floor(secs / 60) + ':' + (secs % 60 < 10 ? '0' : '') + (secs % 60);
      state.textContent = 'working · ' + clock + (state.dataset.counts ? ' · ' + state.dataset.counts : '');
    }
  }, 1000);

  Tomo.renderInspectorStep = function (body, kind, data) {
    var esc = Tomo.escapeHtml;
    var root = Tomo.siTimeline(body);
    var fmt = Tomo.formatToolSummary;
    var preview = Tomo.toolResultPreview;

    if (kind === 'thinking') {
      root.querySelectorAll('.si-streamed-reasoning').forEach(function (card) { card.remove(); });
      var wrap = Tomo.buildReasoningCard(data.content);
      root.appendChild(wrap);
      return wrap;
    }

    if (kind === 'thinking_delta') {
      var reasoning = root.querySelector('.si-streamed-reasoning');
      var text = (reasoning && reasoning._raw || '') + (data.content || '');
      if (!reasoning) {
        reasoning = Tomo.buildReasoningCard(text);
        reasoning.classList.add('si-streamed-reasoning');
        root.appendChild(reasoning);
      } else Tomo.updateReasoningCard(reasoning, text);
      reasoning._raw = text;
      return reasoning;
    }

    if (kind === 'tool') {
      var tcard = Tomo.buildToolCard({
        tool: data.tool || 'tool',
        args: data.args || {},
        running: true,
        call_id: data.call_id || '',
      });
      root.appendChild(tcard);
      return tcard;
    }

    if (kind === 'tool_result') {
      var last = Tomo.findToolCard(body, data) || Tomo.findToolCard(root, data);
      if (last) {
        var resultText = typeof data.result === 'string' ? data.result : JSON.stringify(data.result || '');
        Tomo.finishToolCard(last, resultText, !!data.error);
      }
      if (Array.isArray(data.todos)) Tomo.upsertTodoPanel(root, data.todos);
      return last;
    }

    if (kind === 'tool_output_delta') {
      var card = Tomo.findToolCard(body, data);
      if (card) Tomo.appendToolOutput(card, data.content || '');
      return card;
    }

    if (kind === 'todos') {
      return Tomo.upsertTodoPanel(root, data.todos || []);
    }

    if (kind === 'ui' && window.TomoGenerativeUI) {
      return TomoGenerativeUI.mount(root, data, {});
    }

    if (kind === 'delta' || kind === 'done' || kind === 'subagent_final' || kind === 'final') {
      var answerWrap = body.querySelector('.si-answer');
      if (!answerWrap) {
        answerWrap = document.createElement('section');
        answerWrap.className = 'si-answer';
        answerWrap.innerHTML =
          '<div class="si-answer-label">Answer</div>' +
          '<div class="si-answer-body prose chat-prose"></div>';
        answerWrap._raw = '';
        // Answer sits below the step list, not inside it.
        (body || root).appendChild(answerWrap);
      }
      var bubble = answerWrap.querySelector('.si-answer-body');
      answerWrap._raw = kind === 'delta' ? (answerWrap._raw || '') + (data.content || '') : (data.content || answerWrap._raw || '');
      if (window.TomoChat && TomoChat.setMarkdown) {
        TomoChat.setMarkdown(bubble, answerWrap._raw);
      } else {
        bubble.textContent = answerWrap._raw;
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

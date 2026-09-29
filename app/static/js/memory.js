/* memory.js — Memory page.
 *
 * One call to /api/memory/overview, then three linked views:
 *   list   — every remembered thing, grouped by type, filtered by search
 *   map    — the focused thing in the middle, what it links to around it,
 *            one more hop faintly behind; click to move the focus
 *   reader — its facts in plain text, where each came from, what mentions
 *            it, and a way to forget a fact that's wrong
 * plus a day-by-day timeline underneath.
 */
(function () {
  'use strict';

  var TYPES = ['person', 'project', 'tool', 'place', 'org', 'topic'];
  var esc = function (s) { return window.Tomo && Tomo.escapeHtml ? Tomo.escapeHtml(s) : String(s == null ? '' : s); };
  var root = document.getElementById('mem');
  if (!root) return;

  var els = {
    stats: document.getElementById('memStats'),
    query: document.getElementById('memQuery'),
    list: document.getElementById('memList'),
    map: document.getElementById('memMap'),
    svg: document.getElementById('memGraph'),
    hint: document.getElementById('memMapHint'),
    reader: document.getElementById('memReader'),
    days: document.getElementById('memDays'),
    empty: document.getElementById('memEmpty'),
    body: root.querySelector('.mem-body'),
  };

  var state = {
    byKey: {},        // key -> entity
    adj: {},          // key -> Set of neighbour keys (either direction)
    out: {},          // key -> [keys it links to]
    inc: {},          // key -> [keys linking to it]
    timeline: [],
    focus: null,
    q: '',
    pos: {},          // key -> {x, y} currently drawn
    anim: 0,
  };

  function typeColor(t) {
    return 'var(--mem-type-' + (TYPES.indexOf(t) >= 0 ? t : 'topic') + ')';
  }

  // ── Data ──────────────────────────────────────────────────────────
  function load(keepFocus) {
    return fetch('/api/memory/overview', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (data) {
        index(data);
        var hasAny = Object.keys(state.byKey).length > 0;
        els.empty.hidden = hasAny;
        els.body.hidden = !hasAny;
        if (!hasAny) { renderStats(); renderDays(); return; }
        if (!keepFocus || !state.byKey[state.focus]) state.focus = pickDefaultFocus();
        renderStats();
        renderList();
        renderDays();
        focus(state.focus, { instant: true, noScroll: true });
      })
      .catch(function (err) {
        els.stats.textContent = 'Could not load memory (' + err.message + ').';
      });
  }

  function index(data) {
    state.byKey = {};
    state.adj = {};
    state.out = {};
    state.inc = {};
    (data.entities || []).forEach(function (e) {
      state.byKey[e.key] = e;
      state.adj[e.key] = new Set();
      state.out[e.key] = [];
      state.inc[e.key] = [];
    });
    (data.links || []).forEach(function (l) {
      if (!state.byKey[l.from] || !state.byKey[l.to]) return;
      state.adj[l.from].add(l.to);
      state.adj[l.to].add(l.from);
      if (state.out[l.from].indexOf(l.to) < 0) state.out[l.from].push(l.to);
      if (state.inc[l.to].indexOf(l.from) < 0) state.inc[l.to].push(l.from);
    });
    state.timeline = data.timeline || [];
  }

  function activeFacts(e) { return (e.facts || []).filter(function (f) { return !f.superseded; }); }

  function pickDefaultFocus() {
    var keys = Object.keys(state.byKey);
    // Start where the most threads meet — the richest view on first load.
    keys.sort(function (a, b) {
      return state.adj[b].size - state.adj[a].size ||
        activeFacts(state.byKey[b]).length - activeFacts(state.byKey[a]).length;
    });
    return keys[0];
  }

  // ── Text: [[links]] become buttons ────────────────────────────────
  function resolveLink(target) {
    target = String(target || '').trim().toLowerCase();
    if (state.byKey[target]) return { kind: 'entity', key: target };
    if (/^\d{4}-\d{2}-\d{2}/.test(target)) return { kind: 'day', date: target.slice(0, 10) };
    var hit = Object.keys(state.byKey).filter(function (k) {
      var e = state.byKey[k];
      return e.slug === target || (e.aliases || []).indexOf(target) >= 0;
    })[0];
    return hit ? { kind: 'entity', key: hit } : null;
  }

  function richText(text) {
    var out = '';
    var re = /\[\[([^\]]+)\]\]/g;
    var last = 0;
    var m;
    while ((m = re.exec(text))) {
      out += esc(text.slice(last, m.index));
      var link = resolveLink(m[1].split('#')[0]);
      if (link && link.kind === 'entity') {
        out += '<button type="button" class="mem-link" data-focus="' + esc(link.key) + '">' + esc(state.byKey[link.key].title) + '</button>';
      } else if (link && link.kind === 'day') {
        out += '<button type="button" class="mem-link is-day" data-day="' + esc(link.date) + '">' + esc(link.date) + '</button>';
      } else {
        out += esc(m[1]);
      }
      last = re.lastIndex;
    }
    return out + esc(text.slice(last));
  }

  // ── Stats + list ──────────────────────────────────────────────────
  function renderStats() {
    var keys = Object.keys(state.byKey);
    if (!keys.length) { els.stats.textContent = 'Nothing remembered yet.'; return; }
    var facts = 0;
    var latest = '';
    keys.forEach(function (k) {
      facts += activeFacts(state.byKey[k]).length;
      if (state.byKey[k].updated > latest) latest = state.byKey[k].updated;
    });
    var links = keys.reduce(function (n, k) { return n + state.out[k].length; }, 0);
    els.stats.textContent = keys.length + ' things · ' + facts + ' facts · ' + links + ' connections' +
      (latest ? ' · last updated ' + latest : '');
  }

  function matches(e, q) {
    if (!q) return true;
    var hay = (e.title + ' ' + (e.aliases || []).join(' ') + ' ' + activeFacts(e).map(function (f) { return f.text; }).join(' ')).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }

  function highlight(title, q) {
    if (!q) return esc(title);
    var i = title.toLowerCase().indexOf(q.split(/\s+/)[0]);
    if (i < 0) return esc(title);
    var n = q.split(/\s+/)[0].length;
    return esc(title.slice(0, i)) + '<mark>' + esc(title.slice(i, i + n)) + '</mark>' + esc(title.slice(i + n));
  }

  function renderList() {
    var q = state.q;
    var html = '';
    var shown = 0;
    TYPES.concat(Object.keys(state.byKey).map(function (k) { return state.byKey[k].type; })
      .filter(function (t, i, a) { return TYPES.indexOf(t) < 0 && a.indexOf(t) === i; }))
      .forEach(function (t) {
        var items = Object.keys(state.byKey).filter(function (k) {
          return state.byKey[k].type === t && matches(state.byKey[k], q);
        }).sort(function (a, b) { return state.byKey[a].title.localeCompare(state.byKey[b].title); });
        if (!items.length) return;
        shown += items.length;
        html += '<div class="mem-group"><div class="mem-group-head"><i style="background:' + typeColor(t) + '"></i>' +
          esc(t) + '<span>' + items.length + '</span></div>' +
          items.map(function (k) {
            var e = state.byKey[k];
            return '<button type="button" class="mem-item" data-focus="' + esc(k) + '"' +
              (k === state.focus ? ' aria-current="true"' : '') + '><b>' + highlight(e.title, q) + '</b><small>' +
              activeFacts(e).length + '</small></button>';
          }).join('') + '</div>';
      });
    els.list.innerHTML = html || '<div class="mem-list-empty">No match for “' + esc(q) + '”.</div>';
    return shown;
  }

  // ── Map ───────────────────────────────────────────────────────────
  var SVGNS = 'http://www.w3.org/2000/svg';

  function layout(focusKey, w, h) {
    var cx = w / 2;
    var cy = h / 2;
    var r1 = Math.min(w, h) * 0.30;
    var r2 = Math.min(w, h) * 0.46;
    var pos = {};
    var roles = {};
    pos[focusKey] = { x: cx, y: cy };
    roles[focusKey] = 'focus';
    var near = Array.from(state.adj[focusKey] || []).sort(function (a, b) {
      var ta = TYPES.indexOf(state.byKey[a].type);
      var tb = TYPES.indexOf(state.byKey[b].type);
      return ta - tb || a.localeCompare(b);
    });
    var angleOf = {};
    near.forEach(function (k, i) {
      var a = -Math.PI / 2 + (i / Math.max(1, near.length)) * Math.PI * 2;
      angleOf[k] = a;
      pos[k] = { x: cx + Math.cos(a) * r1, y: cy + Math.sin(a) * r1 };
      roles[k] = 'near';
    });
    // Second hop: fan out behind the neighbour that leads to it.
    var far = [];
    near.forEach(function (k) {
      Array.from(state.adj[k]).sort().forEach(function (k2) {
        if (roles[k2] || far.some(function (f) { return f.key === k2; })) return;
        far.push({ key: k2, via: k });
      });
    });
    far = far.slice(0, 14);
    var perParent = {};
    far.forEach(function (f) { (perParent[f.via] = perParent[f.via] || []).push(f.key); });
    Object.keys(perParent).forEach(function (via) {
      var kids = perParent[via];
      var spread = Math.min(0.9, 0.32 * kids.length);
      kids.forEach(function (k2, i) {
        var off = kids.length === 1 ? 0 : -spread / 2 + (i / (kids.length - 1)) * spread;
        var a = angleOf[via] + off;
        pos[k2] = { x: cx + Math.cos(a) * r2, y: cy + Math.sin(a) * r2 };
        roles[k2] = 'far';
      });
    });
    // Nothing linked yet: show unrelated things faintly so the map isn't empty.
    if (!near.length) {
      var rest = Object.keys(state.byKey).filter(function (k) { return k !== focusKey; }).slice(0, 10);
      rest.forEach(function (k, i) {
        var a = -Math.PI / 2 + (i / Math.max(1, rest.length)) * Math.PI * 2;
        pos[k] = { x: cx + Math.cos(a) * r2, y: cy + Math.sin(a) * r2 };
        roles[k] = 'far';
      });
    }
    return { pos: pos, roles: roles, near: near.length, far: far.length };
  }

  function nodeRadius(k, role) {
    var n = activeFacts(state.byKey[k]).length;
    var base = role === 'focus' ? 14 : role === 'near' ? 8 : 5;
    return base + Math.min(8, n * (role === 'focus' ? 1.2 : 0.8));
  }

  function ensureNode(k) {
    var g = els.svg.querySelector('g.mem-node[data-key="' + CSS.escape(k) + '"]');
    if (g) return g;
    var e = state.byKey[k];
    g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('class', 'mem-node');
    g.setAttribute('data-key', k);
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'button');
    g.setAttribute('aria-label', e.title + ', ' + e.type);
    g.innerHTML =
      '<circle class="ring"></circle><circle class="core"></circle>' +
      '<text class="label" text-anchor="middle"></text><text class="sub" text-anchor="middle"></text>';
    g.querySelector('.label').textContent = e.title;
    g.querySelector('.sub').textContent = e.type;
    g.addEventListener('click', function () { focus(k); });
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); focus(k); }
    });
    els.svg.appendChild(g);
    return g;
  }

  function drawMap(opts) {
    opts = opts || {};
    var rect = els.map.getBoundingClientRect();
    var w = Math.max(320, rect.width);
    var h = Math.max(320, rect.height);
    els.svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    var L = layout(state.focus, w, h);
    var keys = Object.keys(L.pos);
    var q = state.q;

    // Remove nodes that left the view.
    els.svg.querySelectorAll('g.mem-node').forEach(function (g) {
      if (!L.pos[g.getAttribute('data-key')]) g.remove();
    });
    var orbits = els.svg.querySelector('g.orbits');
    if (!orbits) {
      orbits = document.createElementNS(SVGNS, 'g');
      orbits.setAttribute('class', 'orbits');
      els.svg.insertBefore(orbits, els.svg.firstChild);
    }
    var ocx = w / 2, ocy = h / 2, om = Math.min(w, h);
    orbits.innerHTML =
      '<circle class="mem-orbit" cx="' + ocx + '" cy="' + ocy + '" r="' + (om * 0.30).toFixed(1) + '"/>' +
      '<circle class="mem-orbit is-far" cx="' + ocx + '" cy="' + ocy + '" r="' + (om * 0.46).toFixed(1) + '"/>';
    var edgeLayer = els.svg.querySelector('g.edges');
    if (!edgeLayer) {
      edgeLayer = document.createElementNS(SVGNS, 'g');
      edgeLayer.setAttribute('class', 'edges');
      els.svg.insertBefore(edgeLayer, els.svg.firstChild);
    }

    var from = {};
    keys.forEach(function (k) {
      from[k] = state.pos[k] || (state.pos[state.focus] ? { x: state.pos[state.focus].x, y: state.pos[state.focus].y } : L.pos[k]);
      var g = ensureNode(k);
      var role = L.roles[k];
      var r = nodeRadius(k, role);
      var e = state.byKey[k];
      g.setAttribute('class', 'mem-node is-' + role + (q && !matches(e, q) ? ' is-dim' : ''));
      var core = g.querySelector('.core');
      core.setAttribute('r', r);
      core.setAttribute('fill', typeColor(e.type));
      var ring = g.querySelector('.ring');
      ring.setAttribute('r', r + 5);
      ring.setAttribute('stroke', typeColor(e.type));
      var label = g.querySelector('.label');
      label.setAttribute('y', r + (role === 'focus' ? 20 : 17));
      var sub = g.querySelector('.sub');
      sub.setAttribute('y', r + (role === 'focus' ? 35 : 31));
      sub.style.display = role === 'far' ? 'none' : '';
    });

    var edges = [];
    keys.forEach(function (a) {
      state.out[a].forEach(function (b) {
        if (!L.pos[b]) return;
        var far = L.roles[a] === 'far' || L.roles[b] === 'far';
        edges.push({ a: a, b: b, far: far });
      });
    });

    function frame(t) {
      var cur = {};
      keys.forEach(function (k) {
        var p0 = from[k];
        var p1 = L.pos[k];
        cur[k] = { x: p0.x + (p1.x - p0.x) * t, y: p0.y + (p1.y - p0.y) * t };
        var g = els.svg.querySelector('g.mem-node[data-key="' + CSS.escape(k) + '"]');
        if (g) g.setAttribute('transform', 'translate(' + cur[k].x.toFixed(1) + ',' + cur[k].y.toFixed(1) + ')');
      });
      edgeLayer.innerHTML = edges.map(function (e) {
        var p = cur[e.a];
        var q2 = cur[e.b];
        var mx = (p.x + q2.x) / 2;
        var my = (p.y + q2.y) / 2;
        // Gentle bow so overlapping edges stay distinguishable.
        var dx = q2.x - p.x;
        var dy = q2.y - p.y;
        var bx = mx - dy * 0.08;
        var by = my + dx * 0.08;
        return '<path class="mem-edge' + (e.far ? ' is-far' : '') + '" d="M' + p.x.toFixed(1) + ',' + p.y.toFixed(1) +
          ' Q' + bx.toFixed(1) + ',' + by.toFixed(1) + ' ' + q2.x.toFixed(1) + ',' + q2.y.toFixed(1) + '"/>';
      }).join('');
      state.pos = cur;
    }

    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (opts.instant || reduce) { frame(1); }
    else {
      var start = performance.now();
      var id = ++state.anim;
      var dur = 520;
      (function tick(now) {
        if (id !== state.anim) return;
        var t = Math.min(1, (now - start) / dur);
        frame(1 - Math.pow(1 - t, 3));
        if (t < 1) requestAnimationFrame(tick);
      })(start);
    }

    var focusE = state.byKey[state.focus];
    els.hint.textContent = L.near
      ? focusE.title + ' connects to ' + L.near + (L.near === 1 ? ' thing' : ' things') + (L.far ? ' · faint: one more step away' : '')
      : focusE.title + " isn't linked to anything yet";
  }

  // ── Reader ────────────────────────────────────────────────────────
  function mentionsOf(e) {
    var needles = ['[[' + e.key + ']]', '[[' + e.slug + ']]'].concat((e.aliases || []).map(function (a) { return '[[' + a + ']]'; }));
    var out = [];
    state.timeline.forEach(function (d) {
      d.items.forEach(function (it) {
        var low = it.toLowerCase();
        if (needles.some(function (n) { return low.indexOf(n) >= 0; })) out.push({ date: d.date, text: it });
      });
    });
    return out;
  }

  function chip(k) {
    var e = state.byKey[k];
    return '<button type="button" class="mem-chip" data-focus="' + esc(k) + '"><i style="background:' + typeColor(e.type) + '"></i>' + esc(e.title) + '</button>';
  }

  function renderReader() {
    var e = state.byKey[state.focus];
    if (!e) { els.reader.innerHTML = '<p class="mem-reader-empty">Pick something on the left.</p>'; return; }
    var live = activeFacts(e);
    var gone = (e.facts || []).filter(function (f) { return f.superseded; });
    var factHtml = function (f) {
      return '<li class="mem-fact' + (f.superseded ? ' is-gone' : '') + '" style="--mem-c:' + typeColor(e.type) + '" data-n="' + f.n + '">' +
        '<div class="mem-fact-text">' + richText(f.text) + '</div>' +
        '<div class="mem-fact-foot">' +
          (f.source ? '<span>from ' + richText('[[' + f.source + ']]') + '</span>' : '<span>no source</span>') +
          '<span class="spacer"></span>' +
          (f.superseded ? '<span>forgotten</span>' : '<button type="button" class="forget" data-forget="' + f.n + '">Forget</button>') +
        '</div></li>';
    };
    var outs = state.out[e.key] || [];
    var ins = (state.inc[e.key] || []).filter(function (k) { return outs.indexOf(k) < 0; });
    var mentions = mentionsOf(e);

    els.reader.innerHTML =
      '<span class="mem-r-type"><i style="background:' + typeColor(e.type) + '"></i>' + esc(e.type) + '</span>' +
      '<h2>' + esc(e.title) + '</h2>' +
      '<div class="mem-r-meta">' +
        (e.updated ? 'Updated ' + esc(e.updated) + ' · ' : '') +
        '<code>' + esc('entities/' + e.type + '/' + e.slug + '.md') + '</code>' +
      '</div>' +
      '<div class="mem-r-sec">What Tomo knows <span>' + live.length + '</span></div>' +
      (live.length ? '<ul class="mem-facts">' + live.map(factHtml).join('') + '</ul>'
        : '<p class="mem-reader-empty" style="padding:0">No facts left on this page.</p>') +
      (outs.length ? '<div class="mem-r-sec">Links to</div><div class="mem-chips">' + outs.map(chip).join('') + '</div>' : '') +
      (ins.length ? '<div class="mem-r-sec">Mentioned by</div><div class="mem-chips">' + ins.map(chip).join('') + '</div>' : '') +
      (mentions.length ? '<div class="mem-r-sec">In the timeline</div><ul class="mem-mentions">' +
        mentions.slice(0, 8).map(function (m) {
          return '<li><time>' + esc(m.date) + '</time>' + richText(m.text) + '</li>';
        }).join('') + '</ul>' : '') +
      (gone.length ? '<details class="mem-raw"><summary>' + gone.length + ' forgotten ' + (gone.length === 1 ? 'fact' : 'facts') +
        '</summary><ul class="mem-facts" style="margin-top:8px">' + gone.map(factHtml).join('') + '</ul></details>' : '') +
      '<details class="mem-raw" data-raw><summary>Markdown source</summary><pre>Loading…</pre></details>';

    var raw = els.reader.querySelector('[data-raw]');
    raw.addEventListener('toggle', function () {
      if (!raw.open || raw.dataset.loaded) return;
      raw.dataset.loaded = '1';
      fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) { raw.querySelector('pre').textContent = d.raw || ''; })
        .catch(function () { raw.querySelector('pre').textContent = 'Could not load the file.'; });
    }, { once: false });
  }

  function askForget(li, n) {
    var e = state.byKey[state.focus];
    if (li.classList.contains('is-confirm')) return;
    li.classList.add('is-confirm');
    var foot = li.querySelector('.mem-fact-foot');
    var prev = foot.innerHTML;
    foot.innerHTML = '<span>Forget this fact? It stays in the file, struck through.</span><span class="spacer"></span>' +
      '<button type="button" class="forget" style="opacity:1;color:var(--danger)" data-yes>Forget</button>' +
      '<button type="button" class="forget" style="opacity:1" data-no>Keep</button>';
    foot.querySelector('[data-no]').addEventListener('click', function () {
      li.classList.remove('is-confirm');
      foot.innerHTML = prev;
    });
    foot.querySelector('[data-yes]').addEventListener('click', function () {
      fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug) + '/forget', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ number: n }),
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return load(true);
      }).catch(function (err) {
        foot.innerHTML = '<span style="color:var(--danger)">Could not forget: ' + esc(err.message) + '</span>';
      });
    });
    foot.querySelector('[data-no]').focus();
  }

  // ── Timeline ──────────────────────────────────────────────────────
  function weekday(date) {
    try { return new Date(date + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short' }); } catch (_) { return ''; }
  }

  function renderDays() {
    if (!state.timeline.length) {
      els.days.innerHTML = '<p class="mem-reader-empty" style="padding:0">No days recorded yet.</p>';
      return;
    }
    els.days.innerHTML = state.timeline.map(function (d) {
      return '<section class="mem-day" data-date="' + esc(d.date) + '"><time>' + esc(d.date) + '<small>' + esc(weekday(d.date)) + '</small></time>' +
        '<ul>' + d.items.map(function (it) { return '<li>' + richText(it) + '</li>'; }).join('') + '</ul></section>';
    }).join('');
  }

  function markDays() {
    var e = state.byKey[state.focus];
    var hits = e ? mentionsOf(e).map(function (m) { return m.date; }) : [];
    els.days.querySelectorAll('.mem-day').forEach(function (d) {
      d.classList.toggle('is-hit', hits.indexOf(d.dataset.date) >= 0);
    });
  }

  // ── Focus / wiring ────────────────────────────────────────────────
  function focus(key, opts) {
    opts = opts || {};
    if (!state.byKey[key]) return;
    state.focus = key;
    els.list.querySelectorAll('.mem-item').forEach(function (b) {
      if (b.dataset.focus === key) {
        b.setAttribute('aria-current', 'true');
        if (!opts.noScroll) b.scrollIntoView({ block: 'nearest' });
      } else b.removeAttribute('aria-current');
    });
    drawMap({ instant: opts.instant });
    renderReader();
    markDays();
    if (history.replaceState) history.replaceState(null, '', '#' + encodeURIComponent(key));
  }

  root.addEventListener('click', function (ev) {
    var f = ev.target.closest('[data-focus]');
    if (f) { focus(f.dataset.focus); return; }
    var d = ev.target.closest('[data-day]');
    if (d) {
      var day = els.days.querySelector('.mem-day[data-date="' + CSS.escape(d.dataset.day) + '"]');
      if (day) {
        day.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        day.classList.add('is-hit');
      }
      return;
    }
    var fg = ev.target.closest('[data-forget]');
    if (fg) askForget(fg.closest('.mem-fact'), Number(fg.dataset.forget));
  });

  els.query.addEventListener('input', function () {
    state.q = els.query.value.trim().toLowerCase();
    renderList();
    drawMap({ instant: true });
  });
  els.query.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') {
      var first = els.list.querySelector('.mem-item');
      if (first) focus(first.dataset.focus);
    } else if (ev.key === 'Escape') {
      els.query.value = '';
      state.q = '';
      renderList();
      drawMap({ instant: true });
      els.query.blur();
    }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === '/' && document.activeElement !== els.query && !/input|textarea/i.test(document.activeElement.tagName)) {
      ev.preventDefault();
      els.query.focus();
    }
  });

  var resizeT = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeT);
    resizeT = setTimeout(function () { if (state.focus) drawMap({ instant: true }); }, 120);
  });

  var fromHash = decodeURIComponent((location.hash || '').slice(1));
  load(false).then(function () {
    if (fromHash && state.byKey[fromHash]) focus(fromHash, { instant: true });
  });
})();

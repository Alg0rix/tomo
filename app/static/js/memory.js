/* memory.js — Memory page.
 *
 * Two views over the vault:
 *   Pages   — index of remembered things (filter by type, sort), a reader
 *             for the focused page's facts with inline correction, and a
 *             small connection map + link list beside it.
 *   Journal — every turn as goal → outcome, newest day first, paged from
 *             /api/memory/journal; an activity heatmap to jump through time
 *             and filters for agent, page and not-yet-distilled days.
 */
(function () {
  'use strict';

  var TYPES = ['person', 'project', 'tool', 'place', 'org', 'topic'];
  var ORIGINS = {
    agent: { label: 'Saved by agent', c: 'var(--text-faint)' },
    extraction: { label: 'Auto-extracted', c: 'var(--info)' },
    consolidation: { label: 'Distilled from journal', c: 'var(--think)' },
    user: { label: 'Edited by you', c: 'var(--ok)' },
  };
  var ICON = {
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    move: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
    forget: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  };
  var PAGE_DAYS = 10;
  var DAY_PREVIEW = 6;

  var esc = function (s) { return window.Tomo && Tomo.escapeHtml ? Tomo.escapeHtml(s) : String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); };
  var root = document.getElementById('mem');
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };

  var els = {
    stats: $('memStats'), query: $('memQuery'),
    tabs: root.querySelectorAll('.mem-tabs [role="tab"]'), pagesN: $('memTabPagesN'), journalN: $('memTabJournalN'),
    pages: $('memPages'), journal: $('memJournal'), empty: $('memEmpty'),
    types: $('memTypes'), sort: root.querySelector('.mem-sort'), list: $('memList'),
    reader: $('memReader'), map: $('memMap'), svg: $('memGraph'), mapNote: $('memMapNote'), sideLinks: $('memSideLinks'),
    heat: $('memHeat'), heatNote: $('memHeatNote'), filters: $('memFilters'), stream: $('memStream'),
  };

  var state = {
    view: 'pages',
    byKey: {}, adj: {}, out: {}, inc: {}, edge: {}, vocab: {}, dups: {},
    activity: [], agents: [],
    focus: null, q: '', type: '', sort: 'name',
    pos: {}, anim: 0,
    recent: {},
    j: { days: [], next: null, loading: false, seq: 0, before: null, entity: '', agent: '', pending: false, loaded: false },
  };

  // ── Small helpers ─────────────────────────────────────────────────
  function typeColor(t) { return 'var(--mem-type-' + (TYPES.indexOf(t) >= 0 ? t : 'topic') + ')'; }
  // Busiest agent gets the first colour, so the common case stays stable.
  var AGENT_COLORS = ['var(--accent)', 'var(--info)', 'var(--ok)', 'var(--think)', 'var(--danger)', 'var(--mem-type-topic)'];
  function agentColor(id) {
    var i = state.agents.map(function (a) { return a.id; }).indexOf(id);
    return i < 0 ? 'var(--text-faint)' : AGENT_COLORS[i % AGENT_COLORS.length];
  }
  function words() { return state.q ? state.q.split(/\s+/).filter(Boolean) : []; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function activeFacts(e) { return (e.facts || []).filter(function (f) { return !f.superseded; }); }

  function parseDay(d) { return new Date(d + 'T12:00:00'); }
  function iso(dt) { return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0'); }
  var TODAY = iso(new Date());
  function addDays(d, n) { var dt = parseDay(d); dt.setDate(dt.getDate() + n); return iso(dt); }
  function relDay(d) {
    if (!d) return '';
    if (d === TODAY) return 'Today';
    if (d === addDays(TODAY, -1)) return 'Yesterday';
    return '';
  }
  function shortDay(d) {
    if (!d) return '';
    var dt = parseDay(d);
    var opts = { day: 'numeric', month: 'short' };
    if (dt.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return dt.toLocaleDateString(undefined, opts);
  }
  function longDay(d) {
    var dt = parseDay(d);
    var opts = { weekday: 'long', day: 'numeric', month: 'long' };
    if (dt.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return dt.toLocaleDateString(undefined, opts);
  }
  function ago(d) {
    if (!d) return '';
    var r = relDay(d);
    if (r) return r.toLowerCase();
    var days = Math.round((parseDay(TODAY) - parseDay(d)) / 864e5);
    return days < 14 ? days + 'd ago' : shortDay(d);
  }

  // Escape `raw`, wrapping search words in <mark>.
  function marked(raw) {
    var ws = words();
    if (!ws.length) return esc(raw);
    var re = new RegExp('(' + ws.map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|') + ')', 'gi');
    return String(raw).split(re).map(function (part, i) { return i % 2 ? '<mark class="mem-hl">' + esc(part) + '</mark>' : esc(part); }).join('');
  }

  // ── Data ──────────────────────────────────────────────────────────
  function loadDuplicates() {
    return fetch('/api/memory/duplicates', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : { groups: [] }; })
      .then(function (d) {
        state.dups = {};
        (d.groups || []).forEach(function (g) { g.keys.forEach(function (k) { state.dups[k] = g; }); });
      })
      .catch(function () { state.dups = {}; });
  }

  function load(keepFocus) {
    return Promise.all([fetch('/api/memory/overview', { credentials: 'same-origin' }), loadDuplicates()])
      .then(function (res) { return res[0]; })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (data) {
        index(data);
        state.recent = {};
        var hasPages = Object.keys(state.byKey).length > 0;
        var hasAny = hasPages || state.activity.length > 0;
        els.empty.hidden = hasAny;
        root.querySelector('.mem-tabs').hidden = !hasAny;
        if (!hasAny) { els.pages.hidden = true; els.journal.hidden = true; renderStats(); return; }
        if (!keepFocus || !state.byKey[state.focus]) state.focus = hasPages ? pickDefaultFocus() : null;
        renderStats();
        renderTypes();
        renderList();
        renderHeat();
        renderFilters();
        if (state.focus) focus(state.focus, { instant: true, noScroll: true, noHash: true });
        else renderReader();
      })
      .catch(function (err) {
        els.stats.textContent = 'Could not load memory (' + err.message + ').';
      });
  }

  function index(data) {
    state.byKey = {}; state.adj = {}; state.out = {}; state.inc = {}; state.edge = {};
    state.vocab = data.relations || {};
    (data.entities || []).forEach(function (e) {
      state.byKey[e.key] = e;
      state.adj[e.key] = new Set();
      state.out[e.key] = [];
      state.inc[e.key] = [];
    });
    (data.links || []).forEach(function (l) {
      if (!state.byKey[l.from] || !state.byKey[l.to]) return;
      state.edge[l.from + '\n' + l.to] = l;
      state.adj[l.from].add(l.to);
      state.adj[l.to].add(l.from);
      if (state.out[l.from].indexOf(l.to) < 0) state.out[l.from].push(l.to);
      if (state.inc[l.to].indexOf(l.from) < 0) state.inc[l.to].push(l.from);
    });
    state.activity = data.activity || [];
    state.agents = data.agents || [];
  }

  function pickDefaultFocus() {
    var keys = Object.keys(state.byKey);
    // Start on whatever's been talked about most lately.
    keys.sort(function (a, b) {
      var ea = state.byKey[a], eb = state.byKey[b];
      return (eb.last_seen || '').localeCompare(ea.last_seen || '') || (eb.mentions || 0) - (ea.mentions || 0) ||
        state.adj[b].size - state.adj[a].size;
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
    text = String(text || '');
    while ((m = re.exec(text))) {
      out += marked(text.slice(last, m.index));
      var link = resolveLink(m[1].split('#')[0]);
      if (link && link.kind === 'entity') {
        var e = state.byKey[link.key];
        out += '<button type="button" class="mem-link" style="--c:' + typeColor(e.type) + '" data-focus="' + esc(link.key) + '" title="Open ' + esc(e.title) + '">' + marked(e.title) + '</button>';
      } else if (link && link.kind === 'day') {
        out += '<button type="button" class="mem-link is-day" data-day="' + esc(link.date) + '">' + esc(shortDay(link.date)) + '</button>';
      } else {
        out += marked(m[1].split('/').pop());
      }
      last = re.lastIndex;
    }
    return out + marked(text.slice(last));
  }

  // ── Header ────────────────────────────────────────────────────────
  function renderStats() {
    var keys = Object.keys(state.byKey);
    var turns = state.activity.reduce(function (n, d) { return n + d.turns; }, 0);
    if (!keys.length && !turns) { els.stats.textContent = 'Nothing remembered yet.'; return; }
    var facts = 0;
    keys.forEach(function (k) { facts += activeFacts(state.byKey[k]).length; });
    var links = keys.reduce(function (n, k) { return n + state.out[k].length; }, 0);
    els.stats.innerHTML = '<b>' + keys.length + '</b> pages · <b>' + facts + '</b> facts · <b>' + links + '</b> links · <b>' +
      turns + '</b> turns over <b>' + state.activity.length + '</b> days';
    els.pagesN.textContent = keys.length;
    els.journalN.textContent = turns;
  }

  function setView(view, opts) {
    opts = opts || {};
    state.view = view;
    root.dataset.view = view;
    els.tabs.forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.view === view)); });
    els.pages.hidden = view !== 'pages';
    els.journal.hidden = view !== 'journal';
    els.query.placeholder = view === 'pages' ? 'Search pages' : 'Search journal';
    if (view === 'journal') {
      if (!state.j.loaded || opts.reload) loadJournal(true);
    } else {
      renderList();
      drawMap({ instant: true });
    }
    if (!opts.noHash) writeHash();
  }

  function writeHash() {
    var h = state.view === 'journal' ? 'journal' + (state.j.entity ? '/' + state.j.entity : '') : (state.focus || '');
    if (history.replaceState) history.replaceState(null, '', h ? '#' + h : location.pathname);
  }

  // ── Pages · index ─────────────────────────────────────────────────
  function matches(e, q) {
    if (!q) return true;
    var hay = (e.title + ' ' + e.key + ' ' + (e.aliases || []).join(' ') + ' ' + activeFacts(e).map(function (f) { return f.text; }).join(' ')).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }

  function allTypes() {
    var extra = [];
    Object.keys(state.byKey).forEach(function (k) {
      var t = state.byKey[k].type;
      if (TYPES.indexOf(t) < 0 && extra.indexOf(t) < 0) extra.push(t);
    });
    return TYPES.concat(extra);
  }

  function renderTypes() {
    var counts = {};
    Object.keys(state.byKey).forEach(function (k) { counts[state.byKey[k].type] = (counts[state.byKey[k].type] || 0) + 1; });
    var html = '<button type="button" data-type="" aria-pressed="' + (!state.type) + '">All <small>' + Object.keys(state.byKey).length + '</small></button>';
    allTypes().forEach(function (t) {
      if (!counts[t]) return;
      html += '<button type="button" data-type="' + esc(t) + '" aria-pressed="' + (state.type === t) + '"><i class="mem-dot" style="--c:' + typeColor(t) + '"></i>' + esc(t) + ' <small>' + counts[t] + '</small></button>';
    });
    els.types.innerHTML = html;
  }

  function itemHtml(k, sub) {
    var e = state.byKey[k];
    var n = activeFacts(e).length;
    return '<button type="button" class="mem-item" data-focus="' + esc(k) + '"' + (k === state.focus ? ' aria-current="true"' : '') + '>' +
      '<i class="mem-dot" style="--c:' + typeColor(e.type) + '"></i><b>' + marked(e.title) + '</b>' +
      '<small title="' + plural(n, 'fact') + '">' + n + '</small>' + (sub ? '<em>' + sub + '</em>' : '') + '</button>';
  }

  function visibleKeys() {
    var q = state.q;
    return Object.keys(state.byKey).filter(function (k) {
      var e = state.byKey[k];
      return (!state.type || e.type === state.type) && matches(e, q);
    });
  }

  function renderList() {
    var keys = visibleKeys();
    var html = '';
    if (state.sort === 'name') {
      allTypes().forEach(function (t) {
        var items = keys.filter(function (k) { return state.byKey[k].type === t; })
          .sort(function (a, b) { return state.byKey[a].title.localeCompare(state.byKey[b].title); });
        if (!items.length) return;
        html += '<div class="mem-group"><div class="mem-group-head"><i class="mem-dot" style="--c:' + typeColor(t) + '"></i>' + esc(t) + '<span>' + items.length + '</span></div>' +
          items.map(function (k) { return itemHtml(k); }).join('') + '</div>';
      });
    } else {
      var by = state.sort === 'recent'
        ? function (a, b) { return (state.byKey[b].last_seen || state.byKey[b].updated || '').localeCompare(state.byKey[a].last_seen || state.byKey[a].updated || ''); }
        : function (a, b) { return (state.byKey[b].mentions || 0) - (state.byKey[a].mentions || 0); };
      html = keys.sort(function (a, b) { return by(a, b) || state.byKey[a].title.localeCompare(state.byKey[b].title); }).map(function (k) {
        var e = state.byKey[k];
        var sub = state.sort === 'recent'
          ? (e.last_seen ? 'mentioned ' + ago(e.last_seen) : e.updated ? 'updated ' + ago(e.updated) : 'no activity')
          : (e.mentions ? plural(e.mentions, 'mention') + ' in journal' : 'not in journal');
        return itemHtml(k, esc(sub));
      }).join('');
    }
    els.list.innerHTML = html || '<div class="mem-list-empty">No page matches' + (state.q ? ' “' + esc(state.q) + '”' : '') + '.' +
      (state.q ? '<br><button type="button" class="mem-textbtn" data-goto-journal>Search the journal instead →</button>' : '') + '</div>';
    return keys.length;
  }

  // ── Connections: typed relations > explicit mentions > automatic ──
  var RANK = { relation: 0, manual: 1, auto: 2 };
  function relLabel(rel, incoming) {
    var label = incoming ? (state.vocab[rel] || rel) : rel;
    return String(label).replace(/_/g, ' ');
  }
  function edgeRank(a, b) {
    var x = state.edge[a + '\n' + b], y = state.edge[b + '\n' + a];
    return Math.min(x ? RANK[x.kind] : 9, y ? RANK[y.kind] : 9);
  }
  function connections(key) {
    var rels = [], mentions = [], auto = [], seen = {};
    var outs = state.out[key] || [], ins = state.inc[key] || [];
    outs.forEach(function (k) {
      var l = state.edge[key + '\n' + k];
      if (l.kind === 'relation') l.rels.forEach(function (r) { rels.push({ key: k, label: relLabel(r), rel: r, own: true }); });
    });
    ins.forEach(function (k) {
      var l = state.edge[k + '\n' + key];
      if (l.kind === 'relation') l.rels.forEach(function (r) { rels.push({ key: k, label: relLabel(r, true), rel: r }); });
    });
    rels.forEach(function (c) { seen[c.key] = 1; });
    outs.concat(ins).forEach(function (k) {
      if (seen[k]) return;
      seen[k] = 1;
      var rank = edgeRank(key, k);
      if (rank === RANK.manual) mentions.push(k);
      else if (rank === RANK.auto) auto.push(k);
    });
    return { rels: rels, mentions: mentions, auto: auto };
  }
  function autoReason(key, k) {
    var l = state.edge[key + '\n' + k];
    if (l && l.via) return 'says “' + l.via + '”';
    l = state.edge[k + '\n' + key];
    if (l && l.via) return 'mentions “' + l.via + '”';
    return 'name match';
  }

  function connectionsHtml(e, chips) {
    var c = connections(e.key);
    // Side panel: list rows with the label underneath. Reader (narrow screens): chips.
    var item = function (k, sub) {
      if (!chips) return itemHtml(k, esc(sub));
      var p = state.byKey[k];
      return '<button type="button" class="mem-chip" data-focus="' + esc(k) + '"><i class="mem-dot" style="--c:' + typeColor(p.type) + '"></i>' +
        esc(p.title) + (sub ? ' <small>' + esc(sub) + '</small>' : '') + '</button>';
    };
    var sec = function (title, n) {
      return chips ? '<div class="mem-r-sec"><h3>' + title + '</h3><span>' + n + '</span></div>'
        : '<div class="mem-side-sec">' + title + '<span>' + n + '</span></div>';
    };
    var wrap = function (rows) { return chips ? '<div class="mem-chips">' + rows + '</div>' : rows; };
    var html = '';
    if (c.rels.length) {
      html += sec('Relations', c.rels.length) + wrap(c.rels.map(function (r) {
        return '<div class="mem-conn">' + item(r.key, r.label) +
          (r.own ? '<button type="button" class="mem-conn-x" data-unrelate="' + esc(r.rel) + '" data-to="' + esc(r.key) +
            '" title="Remove relation" aria-label="Remove relation ' + esc(r.label) + ' ' + esc(state.byKey[r.key].title) + '">×</button>' : '') +
          '</div>';
      }).join(''));
    }
    if (c.mentions.length) {
      html += sec('Mentions', c.mentions.length) + wrap(c.mentions.map(function (k) {
        return item(k, (state.out[e.key] || []).indexOf(k) >= 0 ? 'linked here' : 'links here');
      }).join(''));
    }
    if (c.auto.length) {
      html += '<details class="mem-auto"' + (c.rels.length + c.mentions.length ? '' : ' open') + '><summary>Also mentioned <span>' + c.auto.length + '</span></summary>' +
        '<p class="mem-auto-note">Linked automatically because a name appears in a fact.</p>' +
        wrap(c.auto.map(function (k) { return item(k, autoReason(e.key, k)); }).join('')) + '</details>';
    }
    return html;
  }

  // ── Pages · map (focus + neighbours + a faint second ring) ────────
  var SVGNS = 'http://www.w3.org/2000/svg';
  var MAP_NEAR = 10, MAP_FAR = 6;

  function layout(focusKey, w, h) {
    var cx = w / 2, cy = h / 2;
    var r1 = Math.min(w, h) * 0.30;
    var r2 = Math.min(w, h) * 0.45;
    var pos = {}, roles = {}, angleOf = {};
    pos[focusKey] = { x: cx, y: cy };
    roles[focusKey] = 'focus';
    // Strongest connections claim the ring first; automatic mentions fill what's left.
    var near = Array.from(state.adj[focusKey] || []).sort(function (a, b) {
      return edgeRank(focusKey, a) - edgeRank(focusKey, b) ||
        TYPES.indexOf(state.byKey[a].type) - TYPES.indexOf(state.byKey[b].type) || a.localeCompare(b);
    }).slice(0, MAP_NEAR);
    near.forEach(function (k, i) {
      var a = -Math.PI / 2 + (i / Math.max(1, near.length)) * Math.PI * 2;
      angleOf[k] = a;
      pos[k] = { x: cx + Math.cos(a) * r1, y: cy + Math.sin(a) * r1 };
      roles[k] = 'near';
    });
    var far = [];
    near.forEach(function (k) {
      Array.from(state.adj[k]).sort().forEach(function (k2) {
        if (roles[k2] || far.some(function (f) { return f.key === k2; })) return;
        if (edgeRank(k, k2) === RANK.auto) return;
        far.push({ key: k2, via: k });
      });
    });
    far = far.slice(0, MAP_FAR);
    var perParent = {};
    far.forEach(function (f) { (perParent[f.via] = perParent[f.via] || []).push(f.key); });
    Object.keys(perParent).forEach(function (via) {
      var kids = perParent[via];
      var spread = Math.min(0.8, 0.3 * kids.length);
      kids.forEach(function (k2, i) {
        var off = kids.length === 1 ? 0 : -spread / 2 + (i / (kids.length - 1)) * spread;
        var a = angleOf[via] + off;
        pos[k2] = { x: cx + Math.cos(a) * r2, y: cy + Math.sin(a) * r2 };
        roles[k2] = 'far';
      });
    });
    return { pos: pos, roles: roles, near: near.length, r1: r1 };
  }

  function nodeRadius(k, role) {
    var n = activeFacts(state.byKey[k]).length;
    return role === 'focus' ? 11 + Math.min(5, n * 0.8) : role === 'near' ? 6 + Math.min(4, n * 0.6) : 4;
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
    g.innerHTML = '<circle class="ring"></circle><circle class="core"></circle><text class="label" text-anchor="middle"></text>';
    g.querySelector('.label').textContent = e.title.length > 18 ? e.title.slice(0, 17) + '…' : e.title;
    g.addEventListener('click', function () { focus(k); });
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); focus(k); }
    });
    els.svg.appendChild(g);
    return g;
  }

  function drawMap(opts) {
    opts = opts || {};
    if (!state.focus || els.pages.hidden || !els.map.offsetParent) return;
    var rect = els.map.getBoundingClientRect();
    var w = Math.max(200, rect.width), h = Math.max(200, rect.height);
    els.svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    var L = layout(state.focus, w, h);
    var keys = Object.keys(L.pos);
    var q = state.q;

    els.svg.querySelectorAll('g.mem-node').forEach(function (g) {
      if (!L.pos[g.getAttribute('data-key')]) g.remove();
    });
    var orbits = els.svg.querySelector('g.orbits');
    if (!orbits) {
      orbits = document.createElementNS(SVGNS, 'g');
      orbits.setAttribute('class', 'orbits');
      els.svg.insertBefore(orbits, els.svg.firstChild);
    }
    orbits.innerHTML = L.near ? '<circle class="mem-orbit" cx="' + (w / 2) + '" cy="' + (h / 2) + '" r="' + L.r1.toFixed(1) + '"/>' : '';
    var edgeLayer = els.svg.querySelector('g.edges');
    if (!edgeLayer) {
      edgeLayer = document.createElementNS(SVGNS, 'g');
      edgeLayer.setAttribute('class', 'edges');
      orbits.after(edgeLayer);
    }

    var from = {};
    keys.forEach(function (k) {
      var fp = state.pos[state.focus];
      from[k] = state.pos[k] || (fp ? { x: fp.x, y: fp.y } : L.pos[k]);
      var g = ensureNode(k);
      var role = L.roles[k];
      var r = nodeRadius(k, role);
      var e = state.byKey[k];
      g.setAttribute('class', 'mem-node is-' + role + (q && !matches(e, q) ? ' is-dim' : ''));
      g.querySelector('.core').setAttribute('r', r);
      g.querySelector('.core').setAttribute('fill', typeColor(e.type));
      g.querySelector('.ring').setAttribute('r', r + 4);
      g.querySelector('.ring').setAttribute('stroke', typeColor(e.type));
      g.querySelector('.label').setAttribute('y', r + 14);
    });

    var edges = [];
    keys.forEach(function (a) {
      state.out[a].forEach(function (b) {
        if (L.pos[b]) edges.push({ a: a, b: b, far: L.roles[a] === 'far' || L.roles[b] === 'far', kind: state.edge[a + '\n' + b].kind });
      });
    });

    function frame(t) {
      var cur = {};
      keys.forEach(function (k) {
        var p0 = from[k], p1 = L.pos[k];
        cur[k] = { x: p0.x + (p1.x - p0.x) * t, y: p0.y + (p1.y - p0.y) * t };
        var g = els.svg.querySelector('g.mem-node[data-key="' + CSS.escape(k) + '"]');
        if (g) g.setAttribute('transform', 'translate(' + cur[k].x.toFixed(1) + ',' + cur[k].y.toFixed(1) + ')');
      });
      edgeLayer.innerHTML = edges.map(function (e) {
        var p = cur[e.a], q2 = cur[e.b];
        var dx = q2.x - p.x, dy = q2.y - p.y;
        var bx = (p.x + q2.x) / 2 - dy * 0.08, by = (p.y + q2.y) / 2 + dx * 0.08;
        return '<path class="mem-edge is-' + e.kind + (e.far ? ' is-far' : '') + '" d="M' + p.x.toFixed(1) + ',' + p.y.toFixed(1) +
          ' Q' + bx.toFixed(1) + ',' + by.toFixed(1) + ' ' + q2.x.toFixed(1) + ',' + q2.y.toFixed(1) + '"/>';
      }).join('');
      state.pos = cur;
    }

    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (opts.instant || reduce) frame(1);
    else {
      var start = performance.now();
      var id = ++state.anim;
      (function tick(now) {
        if (id !== state.anim) return;
        var t = Math.min(1, (now - start) / 480);
        frame(1 - Math.pow(1 - t, 3));
        if (t < 1) requestAnimationFrame(tick);
      })(start);
    }
    var total = state.adj[state.focus].size;
    els.mapNote.textContent = !L.near ? 'not linked yet' : total > L.near ? L.near + ' of ' + plural(total, 'link') : plural(total, 'link');
  }

  function renderSideLinks() {
    var e = state.byKey[state.focus];
    if (!e) { els.sideLinks.innerHTML = ''; return; }
    els.sideLinks.innerHTML = connectionsHtml(e, false) ||
      '<p class="mem-side-empty">Nothing links here yet. Links appear when a fact mentions another page, like [[project/tomo]], or when you add a relation.</p>';
  }

  // ── Pages · reader ────────────────────────────────────────────────
  function factHtml(e, f) {
    var o = ORIGINS[f.origin] || ORIGINS.agent;
    var src = f.source ? f.source.split('#')[0] : '';
    return '<li class="mem-fact' + (f.superseded ? ' is-gone' : '') + '" data-n="' + f.n + '">' +
      '<div class="mem-fact-text">' + richText(f.text) + '</div>' +
      '<div class="mem-fact-foot">' +
        '<span class="mem-origin"><i class="mem-dot" style="--c:' + o.c + '"></i>' + o.label + '</span>' +
        (src ? (/^\d{4}-\d{2}-\d{2}$/.test(src)
          ? '<span>from <button type="button" class="mem-link is-day" data-day="' + esc(src) + '">' + esc(shortDay(src)) + '</button></span>'
          : '<span>from ' + richText('[[' + src + ']]') + '</span>') : '') +
        (f.superseded ? '<span>forgotten</span>' :
          '<span class="mem-fact-actions">' +
            '<button type="button" class="mem-act" data-edit="' + f.n + '" title="Edit" aria-label="Edit this fact">' + ICON.edit + '</button>' +
            '<button type="button" class="mem-act" data-move="' + f.n + '" title="Move to another page" aria-label="Move this fact to another page">' + ICON.move + '</button>' +
            '<button type="button" class="mem-act is-danger" data-forget="' + f.n + '" title="Forget" aria-label="Forget this fact">' + ICON.forget + '</button>' +
          '</span>') +
      '</div></li>';
  }

  function chipRow(keys) {
    return '<div class="mem-chips">' + keys.map(function (k) {
      var e = state.byKey[k];
      return '<button type="button" class="mem-chip" data-focus="' + esc(k) + '"><i class="mem-dot" style="--c:' + typeColor(e.type) + '"></i>' + esc(e.title) + '</button>';
    }).join('') + '</div>';
  }

  function renderReader() {
    var e = state.byKey[state.focus];
    if (!e) {
      els.reader.innerHTML = '<p class="mem-reader-empty">' + (Object.keys(state.byKey).length ? 'Pick a page on the left.' :
        'No pages yet. Tomo is still journaling — facts get distilled into pages each night.') + '</p>';
      return;
    }
    var live = activeFacts(e);
    var gone = (e.facts || []).filter(function (f) { return f.superseded; });
    var others = (e.aliases || []).filter(function (a) { return a !== e.slug && a !== e.title.toLowerCase(); });

    els.reader.innerHTML =
      '<div class="mem-fade">' +
      '<div class="mem-r-type"><i class="mem-dot" style="--c:' + typeColor(e.type) + '"></i>' + esc(e.type) + '</div>' +
      '<h2>' + esc(e.title) + '</h2>' + dupBanner(e) +
      (others.length ? '<div class="mem-r-aliases">Also known as ' + others.map(esc).join(', ') + '</div>' : '') +
      '<div class="mem-r-stats">' +
        '<span><b>' + live.length + '</b> ' + (live.length === 1 ? 'fact' : 'facts') + '</span>' +
        '<span><b>' + (e.mentions || 0) + '</b> journal ' + (e.mentions === 1 ? 'mention' : 'mentions') + '</span>' +
        (e.last_seen ? '<span>last mentioned <b>' + esc(ago(e.last_seen)) + '</b></span>' : '') +
        (e.updated ? '<span>updated ' + esc(shortDay(e.updated)) + '</span>' : '') +
        '<span><code>entities/' + esc(e.type + '/' + e.slug) + '.md</code></span>' +
      '</div>' +
      '<div class="mem-r-sec"><h3>What Tomo knows</h3></div>' +
      (live.length ? '<ol class="mem-facts">' + live.map(function (f) { return factHtml(e, f); }).join('') + '</ol>'
        : '<p class="mem-reader-empty">No facts left on this page.</p>') +
      '<div class="mem-inline-links">' + connectionsHtml(e, true) + '</div>' +
      '<div class="mem-r-tools">' +
        '<button type="button" class="mem-textbtn" data-add-relation>+ Relation</button>' +
        '<button type="button" class="mem-textbtn" data-merge-into>Merge into another page…</button>' +
      '</div>' +
      '<div data-tool-slot></div>' +
      (e.mentions ? '<div class="mem-r-sec"><h3>Recent activity</h3><span>' + e.mentions + '</span>' +
        '<button type="button" class="mem-textbtn" data-journal-entity="' + esc(e.key) + '">Open in journal →</button></div>' +
        '<ul class="mem-activity" data-recent><li><time>…</time><p class="mem-muted">Loading…</p></li></ul>' : '') +
      (gone.length ? '<details class="mem-raw"><summary>' + plural(gone.length, 'forgotten fact') + '</summary><ol class="mem-facts">' +
        gone.map(function (f) { return factHtml(e, f); }).join('') + '</ol></details>' : '') +
      '<details class="mem-raw" data-raw><summary>Markdown source</summary><pre>Loading…</pre></details>' +
      '</div>';

    var raw = els.reader.querySelector('[data-raw]');
    raw.addEventListener('toggle', function () {
      if (!raw.open || raw.dataset.loaded) return;
      raw.dataset.loaded = '1';
      fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) { raw.querySelector('pre').textContent = d.raw || ''; })
        .catch(function () { raw.querySelector('pre').textContent = 'Could not load the file.'; });
    });
    if (e.mentions) loadRecent(e.key);
  }

  function loadRecent(key) {
    var put = function (entries) {
      var ul = els.reader.querySelector('[data-recent]');
      if (!ul || state.focus !== key) return;
      ul.innerHTML = entries.slice(0, 5).map(function (t) {
        return '<li><time>' + esc(shortDay(t.date)) + (t.time ? ' ' + esc(t.time) : '') + '</time><p>' + richText(t.goal || t.outcome || t.notes[0] || '') + '</p></li>';
      }).join('') || '<li><time></time><p class="mem-muted">No recent turns.</p></li>';
    };
    if (state.recent[key]) { put(state.recent[key]); return; }
    fetch('/api/memory/journal?days=3&entity=' + encodeURIComponent(key), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (d) {
        var flat = [];
        d.days.forEach(function (day) { day.entries.slice().reverse().forEach(function (t) { flat.push(Object.assign({ date: day.date }, t)); }); });
        state.recent[key] = flat;
        put(flat);
      })
      .catch(function () { put([]); });
  }

  function pageOptions(exclude) {
    return Object.values(state.byKey).filter(function (page) { return page.key !== exclude; })
      .sort(function (a, b) { return a.title.localeCompare(b.title); }).map(function (page) {
        return '<option value="' + esc(page.key) + '">' + esc(page.title + ' · ' + page.type) + '</option>';
      }).join('');
  }

  function dupBanner(e) {
    var g = state.dups[e.key];
    if (!g) return '';
    var others = g.keys.filter(function (k) { return k !== e.key && state.byKey[k]; });
    if (!others.length) return '';
    return '<div class="mem-dup" role="note"><div><b>Possibly the same thing as</b> ' + others.map(function (k) {
      return '<button type="button" class="mem-link" style="--c:' + typeColor(state.byKey[k].type) + '" data-focus="' + esc(k) + '">' + esc(state.byKey[k].title) +
        ' <small>' + esc(state.byKey[k].type) + '</small></button>';
    }).join(', ') + '</div><div class="mem-dup-acts">' + others.map(function (k) {
      return '<button type="button" class="btn ghost sm" data-merge-from="' + esc(k) + '">Merge ' + esc(state.byKey[k].title) + ' (' + esc(state.byKey[k].type) + ') into this page</button>';
    }).join('') + '</div></div>';
  }

  function post(e, action, body) {
    return fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug) + '/' + action, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || 'HTTP ' + r.status);
        return d;
      });
    });
  }

  function toolForm(html, submitLabel, onSubmit) {
    var slot = els.reader.querySelector('[data-tool-slot]');
    var form = document.createElement('form');
    form.className = 'mem-correction mem-tool';
    form.innerHTML = html + '<div class="mem-correction-actions"><span role="status"></span><button type="button" class="btn ghost sm" data-cancel>Cancel</button>' +
      '<button type="submit" class="btn primary sm">' + submitLabel + '</button></div>';
    slot.replaceChildren(form);
    form.querySelector('[data-cancel]').addEventListener('click', function () { slot.replaceChildren(); });
    form.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); slot.replaceChildren(); } });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      onSubmit(form).catch(function (err) {
        form.querySelector('[role="status"]').textContent = err.message;
        submit.disabled = false;
      });
    });
    (form.querySelector('select,input') || form.querySelector('[type="submit"]')).focus();
    return form;
  }

  function addRelation() {
    var e = state.byKey[state.focus];
    toolForm('<div class="mem-tool-row"><span class="mem-tool-subj">' + esc(e.title) + '</span>' +
      '<label class="mem-tool-rel">Relation<select class="input" name="rel">' + Object.keys(state.vocab).map(function (r) {
        return '<option value="' + esc(r) + '">' + esc(relLabel(r)) + '</option>';
      }).join('') + '</select></label>' +
      '<label class="mem-tool-to">Page<select class="input" name="to">' + pageOptions(e.key) + '</select></label></div>', 'Add relation', function (form) {
      var to = form.elements.to.value;
      return post(e, 'relations', { rel: form.elements.rel.value, to: to }).then(function () { return load(true); });
    });
  }

  function mergeInto(sourceKey, targetKey) {
    var src = state.byKey[sourceKey], dst = state.byKey[targetKey];
    var e = state.byKey[state.focus];
    var html = targetKey
      ? '<p class="mem-tool-note">Move every fact, name and link from <b>' + esc(src.title) + '</b> <small>' + esc(src.type) +
        '</small> into <b>' + esc(dst.title) + '</b> <small>' + esc(dst.type) + '</small>? ' + esc(src.title) + ' is removed; a backup is kept outside the vault.</p>'
      : '<label>Merge <b>' + esc(e.title) + '</b> into<select class="input" name="into">' + pageOptions(e.key) + '</select></label>' +
        '<p class="mem-tool-note">Facts, names and links move to the chosen page. This page is removed; a backup is kept outside the vault.</p>';
    toolForm(html, 'Merge', function (form) {
      var into = targetKey || form.elements.into.value;
      return post(src || e, 'merge', { into: into }).then(function (d) { return load(true).then(function () { focus(d.into); }); });
    });
  }

  function correctFact(li, n, move) {
    var e = state.byKey[state.focus];
    var fact = e.facts.find(function (f) { return f.n === n; });
    if (!fact || li.classList.contains('is-confirm')) return;
    li.classList.add('is-confirm', 'is-editing');
    var editor = document.createElement('form');
    editor.className = 'mem-correction';
    editor.innerHTML = move
      ? '<label>Move to page<select class="input" name="destination">' +
        Object.values(state.byKey).filter(function (page) { return page.key !== e.key; })
          .sort(function (a, b) { return a.title.localeCompare(b.title); }).map(function (page) {
            return '<option value="' + esc(page.key) + '">' + esc(page.title + ' · ' + page.type) + '</option>';
          }).join('') + '<option value="__new">New page…</option></select></label>' +
        '<label data-new-page hidden>New page key<input class="input" name="newPage" placeholder="project/my-project"></label>'
      : '<label>Correct the fact<textarea class="input" name="text" rows="3" required></textarea></label>';
    editor.innerHTML += '<div class="mem-correction-actions"><span role="status"></span><button type="button" class="btn ghost sm" data-cancel>Cancel</button><button type="submit" class="btn primary sm">' + (move ? 'Move fact' : 'Save') + '</button></div>';
    li.appendChild(editor);
    if (!move) editor.elements.text.value = fact.text;
    else editor.elements.destination.addEventListener('change', function () {
      editor.querySelector('[data-new-page]').hidden = editor.elements.destination.value !== '__new';
    });
    editor.querySelector('[data-cancel]').addEventListener('click', function () { renderReader(); });
    editor.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); renderReader(); }
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); editor.requestSubmit(); }
    });
    editor.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var body = { number: n, expected: fact.text };
      if (move) body.destination = editor.elements.destination.value === '__new' ? editor.elements.newPage.value.trim() : editor.elements.destination.value;
      else body.text = editor.elements.text.value.trim();
      var submit = editor.querySelector('[type="submit"]');
      submit.disabled = true;
      fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug) + (move ? '/move' : '/edit'), {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'Could not save'); });
        return load(true);
      }).then(function () { if (move) focus(body.destination); }).catch(function (err) {
        editor.querySelector('[role="status"]').textContent = err.message;
        submit.disabled = false;
      });
    });
    var field = editor.querySelector('textarea,select');
    field.focus();
    if (field.setSelectionRange && field.value) field.setSelectionRange(field.value.length, field.value.length);
  }

  function askForget(li, n) {
    var e = state.byKey[state.focus];
    if (li.classList.contains('is-confirm')) return;
    li.classList.add('is-confirm');
    var box = document.createElement('div');
    box.className = 'mem-confirm';
    box.innerHTML = '<span>Forget this fact? It stays in the file, struck through.</span>' +
      '<button type="button" class="btn ghost sm" data-no>Keep</button><button type="button" class="btn sm" style="color:var(--danger)" data-yes>Forget</button>';
    li.querySelector('.mem-fact-foot').appendChild(box);
    box.querySelector('[data-no]').addEventListener('click', function () { li.classList.remove('is-confirm'); box.remove(); });
    box.querySelector('[data-yes]').addEventListener('click', function () {
      fetch('/api/memory/entity/' + encodeURIComponent(e.type) + '/' + encodeURIComponent(e.slug) + '/forget', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ number: n }),
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return load(true);
      }).catch(function (err) {
        box.querySelector('span').textContent = 'Could not forget: ' + err.message;
      });
    });
    box.querySelector('[data-no]').focus();
  }

  // ── Journal · heatmap ─────────────────────────────────────────────
  function renderHeat() {
    var byDay = {};
    var max = 0;
    state.activity.forEach(function (d) { byDay[d.date] = d.turns; if (d.turns > max) max = d.turns; });
    var weeks = 18;
    var today = parseDay(TODAY);
    var monOffset = (today.getDay() + 6) % 7;
    var start = new Date(today); start.setDate(today.getDate() - monOffset - (weeks - 1) * 7);
    var cells = '';
    var inRange = 0, turnsInRange = 0;
    for (var w = 0; w < weeks; w++) {
      var first = new Date(start); first.setDate(start.getDate() + w * 7);
      var label = '';
      for (var k = 0; k < 7; k++) {
        var t = new Date(first); t.setDate(first.getDate() + k);
        if (t.getDate() === 1 || (w === 0 && k === 0)) { label = t.toLocaleDateString(undefined, { month: 'short' }); break; }
      }
      cells += '<span class="mem-heat-month">' + (w === weeks - 1 && !label ? '' : esc(label)) + '</span>';
      for (var d = 0; d < 7; d++) {
        var dt = new Date(first); dt.setDate(first.getDate() + d);
        var key = iso(dt);
        var n = byDay[key] || 0;
        if (n) { inRange++; turnsInRange += n; }
        var lvl = n ? Math.max(1, Math.ceil((n / max) * 4)) : 0;
        var future = key > TODAY;
        cells += '<button type="button" class="mem-heat-cell' + (n ? '' : ' is-empty') + (future ? ' is-future' : '') +
          (state.j.before && addDays(state.j.before, -1) === key ? ' is-on' : '') + '" data-l="' + lvl + '"' +
          (n ? ' data-jump="' + key + '"' : ' tabindex="-1"') +
          ' title="' + esc(longDay(key)) + ' — ' + (n ? plural(n, 'turn') : 'no turns') + '" aria-label="' + esc(longDay(key)) + ', ' + plural(n, 'turn') + '"></button>';
      }
    }
    els.heat.innerHTML = '<div class="mem-heat-days"><span></span><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span><span></span><span></span></div>' +
      '<div class="mem-heat-grid" style="grid-template-columns:repeat(' + weeks + ',1fr)">' + cells + '</div>';
    var older = state.activity.length - inRange;
    els.heatNote.textContent = plural(turnsInRange, 'turn') + ' · ' + weeks + ' wks' + (older > 0 ? ' (+' + older + ' older days)' : '');
  }

  // ── Journal · filters ─────────────────────────────────────────────
  function renderFilters() {
    var j = state.j;
    var agentChips = '<button type="button" class="mem-chip" data-agent="" aria-pressed="' + (!j.agent) + '">Everyone</button>' +
      state.agents.map(function (a) {
        return '<button type="button" class="mem-chip" data-agent="' + esc(a.id) + '" aria-pressed="' + (j.agent === a.id) + '"><i class="mem-dot" style="--c:' + agentColor(a.id) + '"></i>' + esc(a.name) + ' <small>' + a.turns + '</small></button>';
      }).join('');
    var mentioned = Object.keys(state.byKey).filter(function (k) { return state.byKey[k].mentions; })
      .sort(function (a, b) { return state.byKey[b].mentions - state.byKey[a].mentions; });
    var ent = j.entity && state.byKey[j.entity];
    var any = j.agent || j.entity || j.pending || j.before;
    els.filters.innerHTML =
      (state.agents.length > 1 ? '<div class="mem-filter"><h4>Agent</h4><div class="mem-chips">' + agentChips + '</div></div>' : '') +
      '<div class="mem-filter"><h4>About</h4>' +
        (ent ? '<div class="mem-chips" style="margin-bottom:8px"><button type="button" class="mem-chip" aria-pressed="true" data-clear-entity title="Remove filter"><i class="mem-dot" style="--c:' + typeColor(ent.type) + '"></i>' + esc(ent.title) + '<span class="x" aria-hidden="true">×</span></button></div>' : '') +
        '<select class="input" data-entity-select aria-label="Only turns about a page"><option value="">' + (ent ? 'Change page…' : 'Any page') + '</option>' +
          mentioned.map(function (k) { return '<option value="' + esc(k) + '"' + (k === j.entity ? ' selected' : '') + '>' + esc(state.byKey[k].title) + ' (' + state.byKey[k].mentions + ')</option>'; }).join('') +
        '</select></div>' +
      '<div class="mem-filter"><label class="mem-toggle"><input type="checkbox" data-pending' + (j.pending ? ' checked' : '') + '> Only days not yet distilled</label></div>' +
      (any ? '<div><button type="button" class="mem-textbtn" data-clear-filters>Clear filters</button></div>' : '');
  }

  // ── Journal · stream ──────────────────────────────────────────────
  function loadJournal(reset) {
    var j = state.j;
    if (!reset && (j.loading || !j.next)) return;
    var seq = ++j.seq;
    j.loading = true;
    var p = new URLSearchParams({ days: String(PAGE_DAYS) });
    var before = reset ? j.before : j.next;
    if (before) p.set('before', before);
    if (j.entity) p.set('entity', j.entity);
    if (j.agent) p.set('agent', j.agent);
    if (j.pending) p.set('pending', 'true');
    if (state.q) p.set('q', state.q);
    if (reset) {
      j.days = [];
      els.stream.innerHTML = streamBar() + '<div class="mem-skel" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>';
      els.stream.scrollTop = 0;
    } else {
      var more = els.stream.querySelector('[data-more]');
      if (more) { more.disabled = true; more.textContent = 'Loading…'; }
    }
    return fetch('/api/memory/journal?' + p, { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        if (seq !== j.seq) return;
        j.loaded = true;
        j.loading = false;
        j.next = d.next;
        j.days = j.days.concat(d.days);
        if (reset) els.stream.innerHTML = streamBar();
        var end = els.stream.querySelector('.mem-stream-end');
        if (end) end.remove();
        els.stream.insertAdjacentHTML('beforeend', d.days.map(dayHtml).join('') + streamEnd());
        observeEnd();
      })
      .catch(function (err) {
        if (seq !== j.seq) return;
        j.loading = false;
        els.stream.innerHTML = streamBar() + '<div class="mem-stream-empty"><h3>Could not load the journal</h3>' + esc(err.message) + '</div>';
      });
  }

  function streamBar() {
    var j = state.j;
    if (!j.before) return '';
    return '<div class="mem-stream-bar mem-day"><span>Showing ' + esc(longDay(addDays(j.before, -1))) + ' and earlier.</span>' +
      '<button type="button" class="mem-textbtn" data-latest>Back to latest ↑</button></div>';
  }

  function streamEnd() {
    var j = state.j;
    if (!j.days.length) {
      var filtered = j.agent || j.entity || j.pending || state.q;
      return '<div class="mem-stream-empty mem-stream-end"><h3>' + (filtered ? 'No turns match' : 'The journal is empty') + '</h3>' +
        (filtered ? 'Try a different search or <button type="button" class="mem-textbtn" data-clear-filters>clear filters</button>.' : 'Turns show up here as you chat with Tomo.') + '</div>';
    }
    if (j.next) return '<div class="mem-stream-end"><button type="button" class="btn ghost sm" data-more>Load older days</button></div>';
    return '<div class="mem-stream-end">That’s the beginning — ' + plural(j.days.length, 'day') + ' shown.</div>';
  }

  var endObserver = null;
  function observeEnd() {
    if (!('IntersectionObserver' in window)) return;
    if (!endObserver) {
      endObserver = new IntersectionObserver(function (items) {
        if (items.some(function (i) { return i.isIntersecting; })) loadJournal(false);
      }, { root: window.innerWidth >= 900 ? els.stream : null, rootMargin: '400px' });
    }
    endObserver.disconnect();
    var more = els.stream.querySelector('[data-more]');
    if (more) endObserver.observe(more);
  }

  function turnHtml(t, hidden) {
    var agent = t.agent ? '<span class="mem-agent"><i class="mem-dot" style="--c:' + agentColor(t.agent) + '"></i>' + esc(t.agent_name || t.agent) + '</span>' : '';
    var session = t.session
      ? (t.session_exists
        ? '<a class="mem-session" href="/sessions?s=' + encodeURIComponent(t.session) + '" title="Open this chat">↗ ' + esc(t.session_title || 'Open chat') + '</a>'
        : '')
      : '';
    var long = (t.outcome || '').length > 170 || (t.notes || []).length > 2;
    var head = t.goal ? '<p class="mem-turn-goal">' + richText(t.goal) + '</p>' : '';
    var outcome = t.outcome ? '<p class="mem-turn-out">' + richText(t.outcome) + '</p>' : '';
    if (!t.goal && !t.outcome && t.notes.length) {
      head = '<p class="mem-turn-goal is-untitled">' + richText(t.notes[0]) + '</p>';
      t = Object.assign({}, t, { notes: t.notes.slice(1) });
    }
    return '<li class="mem-turn"' + (hidden ? ' hidden data-extra' : '') + ' style="--c:' + agentColor(t.agent) + '">' +
      '<span class="mem-turn-time">' + esc(t.time) + '</span><span class="mem-turn-pin" aria-hidden="true"></span>' +
      '<div class="mem-turn-body">' + head + outcome +
        (t.notes.length ? '<ul class="mem-turn-notes">' + t.notes.map(function (n) { return '<li>' + richText(n) + '</li>'; }).join('') + '</ul>' : '') +
        ((agent || session || long) ? '<div class="mem-turn-meta">' + agent + session +
          (long ? '<button type="button" data-expand aria-expanded="false">Show all</button>' : '') + '</div>' : '') +
      '</div></li>';
  }

  function dayHtml(d) {
    var turns = d.entries.slice().reverse();
    var collapse = turns.length > DAY_PREVIEW + 2 && !state.q;
    var rel = relDay(d.date);
    var status = d.consolidated
      ? '<span class="mem-state is-done" title="Durable facts from this day were distilled into Pages">' + ICON.check + 'Distilled</span>'
      : (d.date >= TODAY
        ? '<span class="mem-state is-wait" title="Distilled into Pages after the day ends">' + ICON.clock + 'Distills tonight</span>'
        : '<span class="mem-state is-wait" title="Waiting for the nightly distill">' + ICON.clock + 'Waiting to distill</span>');
    return '<section class="mem-day mem-fade" data-date="' + esc(d.date) + '">' +
      '<header class="mem-day-head"><h3>' + (rel ? '<em>' + rel + '</em>' : '') + esc(longDay(d.date)) + '</h3>' +
        '<span class="mem-day-count">' + plural(turns.length, 'turn') + '</span><span class="spacer"></span>' + status + '</header>' +
      '<ol class="mem-turns">' + turns.map(function (t, i) { return turnHtml(t, collapse && i >= DAY_PREVIEW); }).join('') + '</ol>' +
      (collapse ? '<button type="button" class="mem-textbtn mem-day-more" data-day-more>Show ' + plural(turns.length - DAY_PREVIEW, 'earlier turn') + '</button>' : '') +
      '</section>';
  }

  function jumpTo(date) {
    state.j.before = date && date < TODAY ? addDays(date, 1) : null;
    if (state.view !== 'journal') setView('journal', { noHash: true });
    loadJournal(true);
    renderHeat();
    renderFilters();
    writeHash();
  }

  function setJournalFilter(patch) {
    Object.assign(state.j, patch);
    renderFilters();
    renderHeat();
    if (state.view === 'journal') { loadJournal(true); writeHash(); }
    else { state.j.loaded = false; setView('journal'); }
  }

  // ── Focus / wiring ────────────────────────────────────────────────
  function focus(key, opts) {
    opts = opts || {};
    if (!state.byKey[key]) return;
    state.focus = key;
    if (state.view !== 'pages' && !opts.noView) setView('pages', { noHash: true });
    if (state.type && state.byKey[key].type !== state.type) { state.type = ''; renderTypes(); renderList(); }
    els.list.querySelectorAll('.mem-item').forEach(function (b) {
      if (b.dataset.focus === key) {
        b.setAttribute('aria-current', 'true');
        if (!opts.noScroll) b.scrollIntoView({ block: 'nearest' });
      } else b.removeAttribute('aria-current');
    });
    drawMap({ instant: opts.instant });
    renderSideLinks();
    renderReader();
    if (!opts.instant) els.reader.scrollTop = 0;
    if (!opts.noHash) writeHash();
  }

  root.addEventListener('click', function (ev) {
    var t = ev.target;
    var tab = t.closest('.mem-tabs [role="tab"]');
    if (tab) { setView(tab.dataset.view); return; }
    var type = t.closest('[data-type]');
    if (type) { state.type = type.dataset.type; renderTypes(); renderList(); return; }
    var sort = t.closest('[data-sort]');
    if (sort) {
      state.sort = sort.dataset.sort;
      els.sort.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', String(b === sort)); });
      renderList();
      return;
    }
    if (t.closest('[data-goto-journal]')) { setView('journal', { reload: true }); return; }
    var je = t.closest('[data-journal-entity]');
    if (je) { setJournalFilter({ entity: je.dataset.journalEntity, before: null }); return; }
    var f = t.closest('[data-focus]');
    if (f) {
      focus(f.dataset.focus);
      if (window.innerWidth < 900 && f.closest('.mem-list')) els.reader.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    var jump = t.closest('[data-jump]');
    if (jump) { jumpTo(jump.dataset.jump); return; }
    var d = t.closest('[data-day]');
    if (d) { jumpTo(d.dataset.day); return; }
    if (t.closest('[data-latest]')) { jumpTo(null); return; }
    var ag = t.closest('[data-agent]');
    if (ag) { setJournalFilter({ agent: ag.dataset.agent }); return; }
    if (t.closest('[data-clear-entity]')) { setJournalFilter({ entity: '' }); return; }
    if (t.closest('[data-clear-filters]')) {
      els.query.value = ''; state.q = '';
      setJournalFilter({ entity: '', agent: '', pending: false, before: null });
      return;
    }
    if (t.closest('[data-more]')) { loadJournal(false); return; }
    var dm = t.closest('[data-day-more]');
    if (dm) {
      dm.closest('.mem-day').querySelectorAll('[data-extra]').forEach(function (li) { li.hidden = false; });
      dm.remove();
      return;
    }
    var ex = t.closest('[data-expand]');
    if (ex) {
      var turn = ex.closest('.mem-turn');
      var open = turn.classList.toggle('is-open');
      ex.textContent = open ? 'Show less' : 'Show all';
      ex.setAttribute('aria-expanded', String(open));
      return;
    }
    if (t.closest('form')) return;
    if (t.closest('[data-add-relation]')) { addRelation(); return; }
    if (t.closest('[data-merge-into]')) { mergeInto(state.focus, null); return; }
    var mf = t.closest('[data-merge-from]');
    if (mf) { mergeInto(mf.dataset.mergeFrom, state.focus); return; }
    var ur = t.closest('[data-unrelate]');
    if (ur) {
      ur.disabled = true;
      post(state.byKey[state.focus], 'relations', { rel: ur.dataset.unrelate, to: ur.dataset.to, remove: true })
        .then(function () { return load(true); }).catch(function () { ur.disabled = false; });
      return;
    }
    var edit = t.closest('[data-edit], [data-move]');
    if (edit) { correctFact(edit.closest('.mem-fact'), Number(edit.dataset.edit || edit.dataset.move), edit.hasAttribute('data-move')); return; }
    var fg = t.closest('[data-forget]');
    if (fg) askForget(fg.closest('.mem-fact'), Number(fg.dataset.forget));
  });

  root.addEventListener('change', function (ev) {
    if (ev.target.matches('[data-entity-select]')) { if (ev.target.value) setJournalFilter({ entity: ev.target.value }); }
    else if (ev.target.matches('[data-pending]')) setJournalFilter({ pending: ev.target.checked });
  });

  // Arrow keys walk the page index.
  els.list.addEventListener('keydown', function (ev) {
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    var items = Array.from(els.list.querySelectorAll('.mem-item'));
    var i = items.indexOf(document.activeElement);
    if (i < 0) return;
    ev.preventDefault();
    var next = items[Math.max(0, Math.min(items.length - 1, i + (ev.key === 'ArrowDown' ? 1 : -1)))];
    next.focus();
    focus(next.dataset.focus, { noScroll: true });
  });

  var qTimer = 0;
  els.query.addEventListener('input', function () {
    state.q = els.query.value.trim().toLowerCase();
    if (state.view === 'pages') {
      renderList();
      drawMap({ instant: true });
    } else {
      clearTimeout(qTimer);
      qTimer = setTimeout(function () { loadJournal(true); }, 250);
    }
    state.j.loaded = state.view === 'journal';
  });
  els.query.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && state.view === 'pages') {
      var first = els.list.querySelector('.mem-item');
      if (first) { focus(first.dataset.focus); first.focus(); }
    } else if (ev.key === 'Escape') {
      els.query.value = '';
      els.query.dispatchEvent(new Event('input'));
      els.query.blur();
    }
  });
  document.addEventListener('keydown', function (ev) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (/input|textarea|select/i.test(tag || '') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.key === '/') { ev.preventDefault(); els.query.focus(); }
    else if (ev.key === 'g') setView('pages');
    else if (ev.key === 'j' && state.view !== 'journal') setView('journal');
  });

  var resizeT = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeT);
    resizeT = setTimeout(function () { drawMap({ instant: true }); }, 120);
  });

  // #journal, #journal/<type/slug>, or #<type/slug>
  var fromHash = decodeURIComponent((location.hash || '').slice(1));
  load(false).then(function () {
    if (/^journal(\/|$)/.test(fromHash)) {
      state.j.entity = fromHash.slice(8);
      renderFilters();
      setView('journal', { noHash: true });
    } else if (fromHash && state.byKey[fromHash]) {
      focus(fromHash, { instant: true });
    } else if (!Object.keys(state.byKey).length && state.activity.length) {
      setView('journal', { noHash: true });
    }
  });
})();

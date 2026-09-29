/* swarm_loom.js — the swarm run as a loom.
 *
 * Every agent is a thread running left→right through time. A task is a
 * woven stretch on its agent's thread; a dependency is a stitch from the end
 * of one stretch to the start of another; board messages are arcs thrown
 * between threads; findings are knots. The coordinator owns the top thread.
 *
 *   TomoLoom.fromApi(runsPayload)      → model (latest run) | null
 *   TomoLoom.apply(model, swarmEvent)  → model (mutated live)
 *   TomoLoom.render(host, model, opts) → draws / redraws into host
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var GUTTER = 138, LANE = 34, AXIS = 24, PAD_R = 18;
  var COORD = '__coord__';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function now() { return Date.now() / 1000; }
  // Thread dyes, assigned in order of first appearance so lanes never clash.
  var DYES = ['#7c8cff', '#e0685a', '#3fb8a9', '#d46aa8', '#8fbf5a', '#5fa8e8', '#c98a4b', '#b48cf0'];
  var dyeOf = {};
  function color(id) {
    if (id === COORD) return 'var(--accent)';
    if (!dyeOf[id]) dyeOf[id] = DYES[Object.keys(dyeOf).length % DYES.length];
    return dyeOf[id];
  }
  function clock(sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m ? m + ':' + (s < 10 ? '0' : '') + s : s + 's';
  }
  function oneLine(s, n) {
    s = String(s || '').replace(/[#*_`>|]/g, '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  // ── Model ──────────────────────────────────────────────
  function emptyModel(runId) {
    return { id: runId, status: 'running', request: '', t0: now(), tEnd: null,
             tasks: [], byId: {}, events: [], agents: {}, plans: [] };
  }

  function taskFor(model, id) {
    if (!id) return null;
    var t = model.byId[id];
    if (!t) {
      t = { id: id, key: id, agent_id: '', agent_name: '', brief: '', depends_on: [],
            status: 'queued', result: '', created: null, started: null, ended: null, dynamic: false };
      model.byId[id] = t;
      model.tasks.push(t);
    }
    return t;
  }

  function ingest(model, kind, p, taskId, at) {
    p = p || {};
    var t;
    switch (kind) {
      case 'run_started':
        model.t0 = Math.min(model.t0, at);
        if (p.request && !model.request) model.request = p.request;
        break;
      case 'task_created':
        t = taskFor(model, taskId || p.task_id);
        t.key = p.key || t.key;
        t.agent_id = p.agent_id || t.agent_id;
        t.agent_name = p.agent_name || t.agent_name;
        t.brief = p.brief || t.brief;
        t.depends_on = p.depends_on || t.depends_on;
        t.dynamic = !!p.dynamic;
        t.created = t.created || at;
        if (!model.plans.length || at - model.plans[model.plans.length - 1] > 1.5) model.plans.push(at);
        break;
      case 'task_started':
        t = taskFor(model, taskId || p.task_id);
        t.status = 'running';
        t.started = t.started || at;
        if (p.agent_name) t.agent_name = p.agent_name;
        break;
      case 'task_done':
        t = taskFor(model, taskId || p.task_id);
        t.status = p.status || 'done';
        t.result = p.content || t.result;
        t.ended = at;
        break;
      case 'task_blocked':
        t = taskFor(model, taskId || p.task_id);
        t.status = 'blocked';
        t.result = p.reason || t.result;
        t.ended = at;
        break;
      case 'finding':
      case 'message':
        model.events.push({ kind: kind, at: at, from: p.agent_id || '', to: p.to_agent_id || '',
                            content: p.content || '', task_id: taskId || '' });
        break;
      case 'run_done':
        model.status = p.status || 'done';
        model.tEnd = at;
        break;
    }
  }

  function fromApi(data) {
    var run = data && data.runs && data.runs[0];
    if (!run) return null;
    var m = emptyModel(run.id);
    m.status = run.status || 'running';
    m.request = run.request || '';
    m.t0 = run.created_at || now();
    (data.agents || []).forEach(function (a) { m.agents[a.id] = a.name; });
    (run.events || []).forEach(function (e) {
      ingest(m, e.kind, e.payload, e.task_id, e.created_at || m.t0);
    });
    // Rows are authoritative for status/result; events only give timing.
    (run.tasks || []).forEach(function (row) {
      var t = taskFor(m, row.id);
      t.agent_id = t.agent_id || row.agent_id;
      t.brief = t.brief || row.brief;
      t.status = row.status || t.status;
      t.result = row.result || t.result;
      t.created = t.created || row.created_at;
      if (t.status !== 'queued' && t.status !== 'running' && !t.ended) t.ended = row.updated_at;
      if (!t.depends_on.length && row.depends_on) t.depends_on = row.depends_on;
    });
    if (m.status !== 'running' && !m.tEnd) m.tEnd = run.updated_at || null;
    return m;
  }

  function apply(model, d) {
    if (!d || !d.run_id) return model;
    if (!model || model.id !== d.run_id) model = emptyModel(d.run_id);
    ingest(model, d.kind, d, d.task_id, now());
    return model;
  }

  // ── Derived ────────────────────────────────────────────
  function nameOf(model, id) {
    if (id === COORD) return 'Coordinator';
    var t = model.tasks.find(function (x) { return x.agent_id === id && x.agent_name; });
    return (t && t.agent_name) || model.agents[id] || id;
  }

  function laneOf(model, id) {
    return model.tasks.some(function (t) { return t.agent_id === id; }) ? id : COORD;
  }

  function lanes(model) {
    var order = [COORD];
    model.tasks.forEach(function (t) {
      if (t.agent_id && order.indexOf(t.agent_id) < 0) order.push(t.agent_id);
    });
    return order;
  }

  function span(model) {
    var end = model.status === 'running' ? now() : (model.tEnd || now());
    model.tasks.forEach(function (t) { end = Math.max(end, t.ended || 0); });
    return { t0: model.t0, t1: Math.max(end, model.t0 + 8) };
  }

  function counts(model) {
    var c = { done: 0, running: 0, failed: 0, queued: 0 };
    model.tasks.forEach(function (t) {
      if (t.status === 'done') c.done++;
      else if (t.status === 'running') c.running++;
      else if (t.status === 'queued') c.queued++;
      else c.failed++;
    });
    return c;
  }

  // ── Canvas ─────────────────────────────────────────────
  function drawCanvas(svg, model, state) {
    var width = Math.max(420, svg.parentNode.clientWidth || 700);
    var order = lanes(model);
    var height = AXIS + order.length * LANE + 10;
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.setAttribute('height', height);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var sp = span(model), usable = width - GUTTER - PAD_R;
    function x(t) { return GUTTER + Math.max(0, Math.min(1, ((t || sp.t0) - sp.t0) / (sp.t1 - sp.t0))) * usable; }
    function y(id) { return AXIS + order.indexOf(id) * LANE + LANE / 2; }

    var defs = el('defs', {}, svg);
    var hatch = el('pattern', { id: 'loomHatch', width: 6, height: 6, patternUnits: 'userSpaceOnUse',
                                patternTransform: 'rotate(45)' }, defs);
    el('rect', { width: 6, height: 6, fill: 'color-mix(in srgb, var(--danger) 22%, transparent)' }, hatch);
    el('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: 'var(--danger)', 'stroke-width': 2, 'stroke-opacity': .55 }, hatch);

    // Axis ticks: pick a step that gives ~5 ticks.
    var total = sp.t1 - sp.t0, steps = [5, 10, 15, 30, 60, 120, 300, 600, 1800];
    var step = steps.find(function (s) { return total / s <= 6; }) || 3600;
    var axis = el('g', { class: 'loom-axis' }, svg);
    for (var s = 0; s <= total; s += step) {
      var tx = x(sp.t0 + s);
      el('line', { x1: tx, x2: tx, y1: AXIS - 6, y2: height - 6, class: 'loom-grid' }, axis);
      el('text', { x: tx, y: 12, 'text-anchor': s ? 'middle' : 'start' }, axis).textContent = clock(s);
    }

    // Warp threads + labels.
    var warp = el('g', { class: 'loom-warp' }, svg);
    order.forEach(function (id) {
      var ly = y(id), c = color(id);
      var g = el('g', { class: 'loom-lane' + (id === COORD ? ' is-coord' : '') }, warp);
      el('line', { x1: GUTTER - 8, x2: width - PAD_R + 6, y1: ly, y2: ly, stroke: c, class: 'loom-thread' }, g);
      var dynamic = model.tasks.some(function (t) { return t.agent_id === id && t.dynamic; });
      el('circle', { cx: 12, cy: ly, r: 4.5, fill: dynamic ? 'none' : c, stroke: c,
                     'stroke-width': dynamic ? 1.6 : 0 }, g);
      var label = el('text', { x: 24, y: ly + 4, class: 'loom-name' }, g);
      label.textContent = oneLine(nameOf(model, id), 16);
      el('title', {}, label).textContent = nameOf(model, id) + (dynamic ? ' · spawned for this chat' : '');
    });

    var byKey = {};
    model.tasks.forEach(function (t) { byKey[t.key] = t; });
    // A queued task starts where its last prerequisite ends (or at "now").
    function startOf(t) {
      if (t.started) return t.started;
      var a = t.created || sp.t0;
      (t.depends_on || []).forEach(function (k) { var p = byKey[k]; if (p) a = Math.max(a, p.ended || sp.t1); });
      return a;
    }

    // Stitches: prerequisite end → dependent start.
    var stitches = el('g', { class: 'loom-stitches' }, svg);
    model.tasks.forEach(function (t) {
      (t.depends_on || []).forEach(function (k) {
        var p = byKey[k];
        if (!p || !p.agent_id || !t.agent_id) return;
        var x1 = x(p.ended || sp.t1), y1 = y(p.agent_id);
        var x2 = x(startOf(t)), y2 = y(t.agent_id);
        var bend = Math.max(12, Math.abs(x2 - x1) / 2);
        el('path', { d: 'M' + x1 + ',' + y1 + ' C' + (x1 + bend) + ',' + y1 + ' ' + (x2 - bend) + ',' + y2 + ' ' + x2 + ',' + y2,
                     class: 'loom-stitch' + (p.status === 'done' ? '' : ' is-waiting') }, stitches);
      });
    });

    // Coordinator knots: each planning pass, then synthesis.
    var coordY = y(COORD);
    model.plans.forEach(function (at, i) {
      var g = el('g', { class: 'loom-plan' }, svg);
      var px = x(at);
      el('rect', { x: px - 4, y: coordY - 4, width: 8, height: 8, transform: 'rotate(45 ' + px + ' ' + coordY + ')' }, g);
      el('title', {}, g).textContent = (i ? 'Replanned' : 'Planned') + ' at ' + clock(at - sp.t0);
    });
    if (model.tEnd && model.status !== 'running') {
      var sx = x(model.tEnd);
      var sg = el('g', { class: 'loom-synth' }, svg);
      el('circle', { cx: sx, cy: coordY, r: 6 }, sg);
      el('title', {}, sg).textContent = 'Synthesized · ' + model.status;
    }

    // Wefts: one bar per task.
    var bars = el('g', { class: 'loom-bars' }, svg);
    model.tasks.forEach(function (t) {
      if (!t.agent_id) return;
      var ly = y(t.agent_id), c = color(t.agent_id);
      var a = startOf(t);
      var b = t.ended || (t.status === 'running' || t.status === 'queued' ? sp.t1 : a);
      if (t.status === 'queued') b = Math.max(b, a);
      var x2 = Math.max(x(b), x(a) + 10), x1 = Math.min(x(a), x2 - 10);
      var cls = 'loom-bar is-' + (t.status === 'done' ? 'done' : t.status === 'running' ? 'running'
        : t.status === 'queued' ? 'queued' : t.status === 'blocked' || t.status === 'cancelled' ? 'blocked' : 'failed');
      if (state.selected === t.id) cls += ' is-selected';
      var g = el('g', { class: cls, tabindex: 0, role: 'button', 'data-task': t.id,
                        'aria-label': nameOf(model, t.agent_id) + ': ' + oneLine(t.brief, 80) + ' (' + t.status + ')' }, bars);
      el('rect', { x: x1, y: ly - 7, width: x2 - x1, height: 14, rx: 7,
                   fill: /failed|error/.test(cls) ? 'url(#loomHatch)' : c, stroke: c, class: 'loom-weft' }, g);
      if (t.status === 'running') el('circle', { cx: x2, cy: ly, r: 5, fill: c, class: 'loom-shuttle' }, g);
      var room = Math.floor((x2 - x1 - 16) / 6.4);
      if (room >= 6) {
        el('text', { x: x1 + 9, y: ly + 3.6, class: 'loom-bar-label' }, g).textContent = oneLine(t.brief, room);
      }
      else if (t.status === 'queued') {
        el('text', { x: x1 - 6, y: ly + 3.6, 'text-anchor': 'end', class: 'loom-wait' }, g).textContent =
          (t.depends_on || []).length ? 'waits on ' + t.depends_on.length : 'queued';
      }
      el('title', {}, g).textContent = oneLine(t.brief, 200);
    });

    // Board traffic: arcs between threads, knots for findings.
    var traffic = el('g', { class: 'loom-traffic' }, svg);
    model.events.forEach(function (e) {
      var from = laneOf(model, e.from);
      var ex = x(e.at), y1 = y(from);
      var g = el('g', { class: 'loom-' + e.kind }, traffic);
      if (e.kind === 'message' && e.to) {
        var y2 = y(laneOf(model, e.to)), bow = 10 + Math.abs(y2 - y1) * 0.25;
        el('path', { d: 'M' + ex + ',' + y1 + ' Q' + (ex + bow) + ',' + ((y1 + y2) / 2) + ' ' + ex + ',' + y2 }, g);
        el('circle', { cx: ex, cy: y2, r: 2.6 }, g);
      } else {
        el('path', { d: 'M' + ex + ',' + (y1 - 11) + ' l4,4 l-4,4 l-4,-4 z' }, g);
      }
      el('title', {}, g).textContent = nameOf(model, from) + (e.to ? ' → ' + nameOf(model, laneOf(model, e.to)) : '') + ': ' + oneLine(e.content, 180);
    });

    if (model.status === 'running') {
      var nx = x(sp.t1);
      el('line', { x1: nx, x2: nx, y1: AXIS - 4, y2: height - 4, class: 'loom-now' }, svg);
    }
  }

  // ── Chrome ─────────────────────────────────────────────
  function detailHtml(model, t) {
    if (!t) return '<p class="loom-hint">Pick a thread to see its task.</p>';
    var dur = t.started ? clock((t.ended || now()) - t.started) : 'waiting';
    var deps = (t.depends_on || []).map(function (k) {
      var p = model.tasks.find(function (x) { return x.key === k; });
      return p ? nameOf(model, p.agent_id) : k;
    });
    var hasTrace = !!document.querySelector('.swarm-row[data-buffer-key="d:' + CSS.escape(t.id) + '"]');
    return '<div class="loom-d-head"><i style="background:' + color(t.agent_id) + '"></i><b>' + esc(nameOf(model, t.agent_id)) + '</b>' +
      (t.dynamic ? '<span class="loom-tag">spawned for this chat</span>' : '') +
      '<span class="loom-d-state is-' + esc(t.status) + '">' + esc(t.status) + ' · ' + esc(dur) + '</span></div>' +
      '<p class="loom-d-brief">' + esc(t.brief) + '</p>' +
      (deps.length ? '<p class="loom-d-deps">After ' + esc(deps.join(', ')) + '</p>' : '') +
      (t.result ? '<p class="loom-d-result">' + esc(oneLine(t.result, 420)) + '</p>' : '') +
      (hasTrace ? '<button type="button" class="loom-trace" data-trace="' + esc(t.id) + '">Open trace</button>' : '');
  }

  function boardHtml(model) {
    var items = model.events.slice(-6).reverse();
    if (!items.length) return '';
    return '<div class="loom-board-head">Board</div><ol class="loom-board">' + items.map(function (e) {
      var from = laneOf(model, e.from);
      return '<li><i style="background:' + color(from) + '"></i><span><b>' + esc(nameOf(model, from)) + '</b>' +
        (e.to ? ' → ' + esc(nameOf(model, laneOf(model, e.to))) : ' <em>found</em>') + ' ' + esc(oneLine(e.content, 160)) + '</span></li>';
    }).join('') + '</ol>';
  }

  function pipsHtml(model) {
    return model.tasks.map(function (t) {
      return '<i class="is-' + esc(t.status) + '" style="--c:' + color(t.agent_id) + '"></i>';
    }).join('');
  }

  function render(host, model, opts) {
    opts = opts || {};
    if (!model) { host.hidden = true; host.innerHTML = ''; host._loom = null; stopTick(host); return; }
    host.hidden = false;
    var st = host._loom || (host._loom = { selected: null, open: null });
    var running = model.status === 'running';
    if (st.open === null || st.lastStatus !== model.status) {
      if (st.userOpen == null) st.open = running;
    }
    st.lastStatus = model.status;
    st.model = model;
    if (!st.selected || !model.byId[st.selected]) {
      var pick = model.tasks.find(function (t) { return t.status === 'running'; }) || model.tasks[model.tasks.length - 1];
      st.selected = pick ? pick.id : null;
    }

    if (!host.querySelector('.loom')) {
      host.innerHTML =
        '<div class="loom">' +
          '<button type="button" class="loom-head" aria-expanded="false">' +
            '<span class="loom-glyph" aria-hidden="true"><i></i><i></i><i></i></span>' +
            '<b>Swarm</b><span class="loom-req"></span><span class="loom-pips" aria-hidden="true"></span>' +
            '<span class="loom-meta"></span><span class="loom-caret" aria-hidden="true"></span>' +
          '</button>' +
          '<div class="loom-body"><div class="loom-canvas"><svg class="loom-svg" role="img"></svg></div>' +
            '<div class="loom-foot"><div class="loom-detail"></div><div class="loom-side"></div></div></div>' +
        '</div>';
    }
    if (!host._loomBound) {
      host._loomBound = true;
      host.addEventListener('click', function (e) {
        var st = host._loom;
        if (!st) return;
        if (e.target.closest('.loom-head')) { st.open = !st.open; st.userOpen = st.open; paint(host); return; }
        var bar = e.target.closest('[data-task]');
        if (bar) { st.selected = bar.getAttribute('data-task'); paint(host); return; }
        var tr = e.target.closest('[data-trace]');
        if (tr) {
          var row = document.querySelector('.swarm-row[data-buffer-key="d:' + CSS.escape(tr.getAttribute('data-trace')) + '"]');
          if (row) { row.scrollIntoView({ block: 'center', behavior: 'smooth' }); (row.querySelector('.sw-open') || row).click(); }
        }
      });
      host.addEventListener('keydown', function (e) {
        var bar = e.target.closest && e.target.closest('[data-task]');
        if (bar && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); bar.dispatchEvent(new MouseEvent('click', { bubbles: true })); }
      });
      if (window.ResizeObserver) new ResizeObserver(function () { if (host._loom && host._loom.open) paint(host); }).observe(host);
    }
    paint(host);
    if (running) startTick(host); else stopTick(host);
  }

  function paint(host) {
    var st = host._loom, model = st.model;
    if (!model) return;
    var root = host.querySelector('.loom');
    var c = counts(model), sp = span(model);
    var agents = lanes(model).length - 1;
    root.dataset.state = model.status;
    root.classList.toggle('is-open', !!st.open);
    root.querySelector('.loom-head').setAttribute('aria-expanded', st.open ? 'true' : 'false');
    root.querySelector('.loom-req').textContent = oneLine(model.request, 90);
    root.querySelector('.loom-pips').innerHTML = pipsHtml(model);
    var bits = [];
    if (!model.tasks.length) bits.push(model.status === 'running' ? 'planning' : 'handled solo');
    else {
      bits.push(agents + (agents === 1 ? ' agent' : ' agents'));
      bits.push(c.done + '/' + model.tasks.length + ' done');
      if (c.failed) bits.push(c.failed + ' failed');
    }
    bits.push(model.status === 'running' ? clock(sp.t1 - sp.t0) : model.status + ' · ' + clock(sp.t1 - sp.t0));
    root.querySelector('.loom-meta').textContent = bits.join(' · ');
    if (!st.open) return;
    var svg = root.querySelector('.loom-svg');
    svg.setAttribute('aria-label', 'Swarm timeline: ' + root.querySelector('.loom-meta').textContent);
    drawCanvas(svg, model, st);
    root.querySelector('.loom-detail').innerHTML = detailHtml(model, model.byId[st.selected]);
    root.querySelector('.loom-side').innerHTML = boardHtml(model);
  }

  function startTick(host) {
    if (host._loomTick) return;
    host._loomTick = setInterval(function () {
      if (!document.body.contains(host)) return stopTick(host);
      paint(host);
    }, 1000);
  }
  function stopTick(host) {
    if (host._loomTick) { clearInterval(host._loomTick); host._loomTick = null; }
  }

  window.TomoLoom = { fromApi: fromApi, apply: apply, render: render };
})();

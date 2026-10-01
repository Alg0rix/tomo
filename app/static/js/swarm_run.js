/* swarm_run.js — the swarm run as a live work board inside its own turn.
 *
 * One card per run, placed where the run happened. The header tells the run's
 * story (plan → work → synthesize) with a segmented meter; each task is an
 * agent lane (the shared .swarm-row from tomo.js) with its dependency, a live
 * last step, and a time track that lines up across lanes like a gantt; the
 * shared board lists findings and steering messages as they land.
 *
 *   TomoSwarm.apply(run, sseData)      → run (created / mutated from a live event)
 *   TomoSwarm.fromApi(apiRun, agents)  → run (from GET /api/sessions/{id}/swarm)
 *   TomoSwarm.mount(card, run, opts)   → paints the run into a .swarm-card
 *   TomoSwarm.lanes(card)              → the lane container rows belong in
 *   TomoSwarm.hydrate(scroll, apiData) → attaches stored runs to history turns
 */
(function () {
  'use strict';

  var ENDED = { done: 1, failed: 1, cancelled: 1, interrupted: 1 };
  var TASK_ENDED = { done: 1, failed: 1, error: 1, blocked: 1, cancelled: 1, interrupted: 1 };

  function esc(s) { return window.Tomo ? Tomo.escapeHtml(s) : String(s == null ? '' : s); }
  function now() { return Date.now() / 1000; }
  function clock(sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  function oneLine(s, n) {
    s = String(s || '').replace(/[#*_`>|]/g, '').replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function cssKey(s) { return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/"/g, '\\"'); }

  // ── Model ──────────────────────────────────────────────
  function emptyRun(id) {
    return { id: id, status: 'running', phase: '', request: '', coordinator: 'Coordinator',
             coordinatorId: '', t0: now(), tEnd: null, tasks: [], byId: {}, board: [], seen: {}, agents: {} };
  }

  function taskFor(run, id) {
    var t = run.byId[id];
    if (!t) {
      t = { id: id, key: id, agent_id: '', agent_name: '', purpose: '', brief: '', depends_on: [],
            status: 'queued', result: '', dynamic: false, created: null, started: null, ended: null };
      run.byId[id] = t;
      run.tasks.push(t);
    }
    return t;
  }

  function ingest(run, kind, p, taskId, at, eventId) {
    p = p || {};
    if (eventId != null) {
      if (run.seen[eventId]) return;
      run.seen[eventId] = 1;
    }
    var t = taskId || p.task_id ? taskFor(run, taskId || p.task_id) : null;
    switch (kind) {
      case 'run_started':
        run.t0 = Math.min(run.t0, at);
        run.request = run.request || p.request || '';
        if (p.coordinator_name) run.coordinator = p.coordinator_name;
        if (p.coordinator_id) run.coordinatorId = p.coordinator_id;
        break;
      case 'phase':
        run.phase = p.phase || run.phase;
        break;
      case 'task_created':
        t.key = p.key || t.key;
        t.agent_id = p.agent_id || t.agent_id;
        t.agent_name = p.agent_name || t.agent_name;
        t.purpose = p.purpose || t.purpose;
        t.brief = p.brief || t.brief;
        t.depends_on = p.depends_on || t.depends_on;
        t.dynamic = !!p.dynamic;
        t.created = t.created || at;
        break;
      case 'task_started':
        t.status = 'running';
        t.started = t.started || at;
        if (p.agent_name) t.agent_name = p.agent_name;
        break;
      case 'task_done':
        t.status = p.status || 'done';
        t.result = p.content || t.result;
        t.ended = at;
        break;
      case 'task_blocked':
        t.status = 'blocked';
        t.result = p.reason || t.result;
        t.ended = at;
        break;
      case 'question_resolved':
        if (t && t.waitingQuestion === p.question_event_id) {
          t.waitingQuestion = null;
          t.questionTimedOut = p.status === 'timeout';
        }
        break;
      case 'coordinator_review_done':
      case 'coordinator_note':
        // Old run summaries also stay out of the board when replayed.
        run.reviewing = false;
        break;
      case 'coordinator_review':
        run.reviewing = true;
        break;
      case 'message_received':
        var source = run.board.find(function (e) { return e.id === p.source_event_id; });
        if (source) {
          source.delivered = source.delivered || [];
          if (source.delivered.indexOf(p.agent_id) < 0) source.delivered.push(p.agent_id);
        }
        break;
      case 'finding':
      case 'message':
      case 'question':
      case 'coordinator_error':
      case 'user_update':
        if (kind === 'question' && t) { t.waitingQuestion = eventId; t.questionTimedOut = false; }
        if (kind === 'coordinator_error') run.reviewing = false;
        run.board.push({ id: eventId, kind: kind, at: at, from: p.agent_id || '', to: p.to_agent_id || '',
                         content: p.content || '' });
        break;
      case 'run_done':
        run.reviewing = false;
        run.status = p.status || 'done';
        run.tEnd = at;
        break;
    }
  }

  function apply(run, d) {
    if (!d || !d.run_id) return run;
    if (!run || run.id !== d.run_id) run = emptyRun(d.run_id);
    ingest(run, d.kind, d, d.task_id, now(), d.event_id);
    return run;
  }

  function fromApi(api, agents) {
    var run = emptyRun(api.id);
    run.status = api.status || 'running';
    run.request = api.request || '';
    run.t0 = api.created_at || now();
    (agents || []).forEach(function (a) { run.agents[a.id] = a; });
    (api.events || []).forEach(function (e) {
      ingest(run, e.kind, e.payload, e.task_id, e.created_at || run.t0, e.id);
    });
    // Rows are authoritative for status and result; events carry the timing.
    (api.tasks || []).forEach(function (row) {
      var t = taskFor(run, row.id);
      t.agent_id = t.agent_id || row.agent_id;
      t.brief = t.brief || row.brief;
      t.status = row.status || t.status;
      t.result = row.result || t.result;
      t.created = t.created || row.created_at;
      if (TASK_ENDED[t.status] && !t.ended) t.ended = row.updated_at;
      if (!t.depends_on.length && row.depends_on) t.depends_on = row.depends_on;
      var local = run.agents[t.agent_id];
      if (local) {
        t.dynamic = true;
        t.agent_name = t.agent_name || local.name;
        t.purpose = t.purpose || local.purpose || '';
      }
    });
    if (ENDED[run.status] && !run.tEnd) run.tEnd = api.updated_at || null;
    return run;
  }

  // ── Derived ────────────────────────────────────────────
  function counts(run) {
    var c = { done: 0, running: 0, awaiting: 0, queued: 0, failed: 0 };
    run.tasks.forEach(function (t) {
      if (t.status === 'done') c.done++;
      else if (t.status === 'running' && t.waitingQuestion) c.awaiting++;
      else if (t.status === 'running') c.running++;
      else if (t.status === 'queued') c.queued++;
      else c.failed++;
    });
    return c;
  }

  function phaseOf(run) {
    if (run.phase === 'solo' || (ENDED[run.status] && !run.tasks.length)) return 'solo';
    if (ENDED[run.status]) return run.status;
    if (run.phase === 'synthesizing') return 'synthesizing';
    if (run.tasks.some(function (t) { return t.started || t.status !== 'queued'; })) return 'working';
    return 'planning';
  }

  function span(run) {
    var end = ENDED[run.status] ? (run.tEnd || now()) : now();
    run.tasks.forEach(function (t) { end = Math.max(end, t.ended || 0); });
    return { t0: run.t0, t1: Math.max(end, run.t0 + 1) };
  }

  // Only the coordinator and its workers post to a run, so an id that owns no
  // lane is the coordinator (older runs did not record its id).
  function isCoordinator(run, id) {
    return !id || id === run.coordinatorId ||
      (!run.agents[id] && !run.tasks.some(function (x) { return x.agent_id === id; }));
  }

  function nameOf(run, id) {
    if (isCoordinator(run, id)) return run.coordinator;
    var t = run.tasks.find(function (x) { return x.agent_id === id && x.agent_name; });
    return (t && t.agent_name) || (run.agents[id] && run.agents[id].name) || id;
  }

  function colorOf(row, aid) {
    return (row && row.style.getPropertyValue('--c')) ||
      (window.Tomo && Tomo.avatarColor ? Tomo.avatarColor(aid || '?') : 'var(--accent)');
  }

  // Hash colours can collide inside one run; give every lane its own hue.
  function assignHues(run, lanesEl) {
    var hues = (window.Tomo && Tomo.avatarHues) || [200, 260, 330, 160, 30, 290, 80, 10];
    var used = {}, byAgent = {};
    run.tasks.forEach(function (t) {
      if (!t.agent_id || byAgent[t.agent_id]) return;
      var base = window.Tomo && Tomo.avatarColor ? Tomo.avatarColor(t.agent_id) : '';
      var m = /hsl\((\d+)/.exec(base);
      var h = m ? +m[1] : hues[0];
      if (used[h]) h = hues.find(function (x) { return !used[x]; }) || h;
      used[h] = 1;
      byAgent[t.agent_id] = 'hsl(' + h + ',62%,46%)';
    });
    lanesEl.querySelectorAll('.swarm-row[data-agent-id]').forEach(function (row) {
      var c = byAgent[row.dataset.agentId];
      if (c && row.style.getPropertyValue('--c') !== c) row.style.setProperty('--c', c);
    });
    return byAgent;
  }

  // ── Card skeleton ──────────────────────────────────────
  var HIVE =
    '<svg class="sr-hive" viewBox="0 0 28 24" aria-hidden="true">' +
      '<path d="M7 2.5h5l2.5 4.3L12 11H7L4.5 6.8z"/>' +
      '<path d="M16 7.5h5l2.5 4.3L21 16h-5l-2.5-4.2z"/>' +
      '<path d="M7 12.5h5l2.5 4.3L12 21H7l-2.5-4.2z"/>' +
    '</svg>';

  function ensure(card) {
    var lanes = card.querySelector(':scope > .sr-lanes');
    if (lanes) return lanes;
    lanes = document.createElement('div');
    lanes.className = 'sr-lanes';
    lanes.setAttribute('role', 'list');
    Array.prototype.slice.call(card.children).forEach(function (child) {
      if (child.classList.contains('swarm-row')) lanes.appendChild(child);
    });
    card.appendChild(lanes);
    return lanes;
  }

  function buildChrome(card) {
    if (card.querySelector(':scope > .sr-head')) return;
    var lanes = ensure(card);
    var head = document.createElement('button');
    head.type = 'button';
    head.className = 'sr-head';
    head.setAttribute('aria-expanded', 'true');
    head.innerHTML =
      '<span class="sr-mark">' + HIVE + '</span>' +
      '<span class="sr-title"><b>Swarm</b><span class="sr-line"></span></span>' +
      '<span class="sr-roster" aria-hidden="true"></span>' +
      '<ol class="sr-phases" aria-label="Run phases">' +
        '<li data-step="plan">Plan</li><li data-step="work">Work</li><li data-step="synth">Synthesize</li>' +
      '</ol>' +
      '<span class="sr-clock"></span>' +
      '<span class="sr-caret" aria-hidden="true"></span>';
    var meter = document.createElement('div');
    meter.className = 'sr-meter';
    meter.setAttribute('aria-hidden', 'true');
    var request = document.createElement('p');
    request.className = 'sr-request';
    var ghost = document.createElement('div');
    ghost.className = 'sr-ghost';
    ghost.innerHTML = '<i></i><i></i><i></i><span>Reading the request and choosing agents…</span>';
    var board = document.createElement('section');
    board.className = 'sr-board';
    board.setAttribute('aria-label', 'Shared board');
    card.insertBefore(head, lanes);
    card.insertBefore(meter, lanes);
    card.insertBefore(request, lanes);
    card.insertBefore(ghost, lanes);
    card.appendChild(board);
    head.addEventListener('click', function () {
      var open = card.classList.toggle('is-collapsed') === false;
      card._srUserToggled = true;
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    board.addEventListener('click', function (e) {
      if (!e.target.closest('.sr-board-more')) return;
      card._srBoardAll = !card._srBoardAll;
      paint(card);
    });
  }

  // ── Paint ──────────────────────────────────────────────
  function headline(run, phase, c) {
    var total = run.tasks.length;
    switch (phase) {
      case 'planning': return 'Planning the team';
      case 'working':
        return [c.running ? c.running + ' working' : '', c.awaiting ? c.awaiting + ' awaiting main' : '',
                c.done ? c.done + ' done' : '',
                c.queued ? c.queued + ' waiting' : '', c.failed ? c.failed + ' failed' : '']
          .filter(Boolean).join(' · ');
      case 'synthesizing': return run.coordinator + ' is synthesizing ' + total + (total === 1 ? ' result' : ' results');
      case 'solo': return 'One agent was enough — ' + run.coordinator + ' answered directly';
      case 'cancelled': return 'Stopped · ' + c.done + ' of ' + total + ' tasks finished';
      case 'interrupted': return 'Interrupted by a restart · ' + c.done + ' of ' + total + ' finished';
      case 'failed': return 'Run failed · ' + c.done + ' of ' + total + ' tasks finished';
      default:
        return c.done + ' of ' + total + ' tasks done' + (c.failed ? ' · ' + c.failed + ' failed' : '');
    }
  }

  function paintHead(card, run, phase, c, colors) {
    var sp = span(run);
    card.dataset.phase = phase;
    card.querySelector('.sr-line').textContent = headline(run, phase, c);
    card.querySelector('.sr-clock').textContent = phase === 'solo' ? '' : clock(sp.t1 - sp.t0);
    var order = { planning: 0, working: 1, synthesizing: 2 };
    var at = order[phase] == null ? 3 : order[phase];
    card.querySelectorAll('.sr-phases li').forEach(function (li, i) {
      li.className = i < at ? 'is-done' : i === at ? 'is-now' : '';
      if (i === at) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
    });
    var req = card.querySelector('.sr-request');
    req.textContent = run.request ? '“' + oneLine(run.request, 220) + '”' : '';
    var seen = {}, roster = '';
    run.tasks.forEach(function (t) {
      if (!t.agent_id || seen[t.agent_id]) return;
      seen[t.agent_id] = 1;
      roster += '<i style="--c:' + (colors[t.agent_id] || colorOf(null, t.agent_id)) + '">' +
        esc((t.agent_name || '?').charAt(0).toUpperCase()) + '</i>';
    });
    card.querySelector('.sr-roster').innerHTML = roster;
    var meter = card.querySelector('.sr-meter');
    var segs = meter.children;
    run.tasks.forEach(function (t, i) {
      var seg = segs[i] || meter.appendChild(document.createElement('i'));
      var st = t.status === 'done' ? 'done' : t.status === 'running' ? 'running'
        : t.status === 'queued' ? 'queued' : 'failed';
      var cls = 'is-' + st;
      if (seg.className !== cls) seg.className = cls;
      seg.style.setProperty('--c', colors[t.agent_id] || colorOf(null, t.agent_id));
      seg.title = (t.agent_name || 'Agent') + ' · ' + t.status;
    });
    while (segs.length > run.tasks.length) meter.removeChild(meter.lastChild);
  }

  function rowFor(lanes, t) {
    var key = 'd:' + t.id;
    return lanes.querySelector('.swarm-row[data-instance-key="' + cssKey(key) + '"]') ||
      lanes.querySelector('.swarm-row[data-buffer-key="' + cssKey(key) + '"]');
  }

  function paintLane(run, t, row, sp) {
    var byKey = {};
    run.tasks.forEach(function (x) { byKey[x.key] = x; });
    row.dataset.taskStatus = t.status;
    row.classList.toggle('is-queued', t.status === 'queued');
    row.classList.toggle('is-blocked', t.status === 'blocked' || t.status === 'cancelled' || t.status === 'interrupted');
    var waiting = !ENDED[run.status] && t.status === 'running' && !!t.waitingQuestion;
    var timedOut = !ENDED[run.status] && t.status === 'running' && !!t.questionTimedOut;
    row.classList.toggle('is-awaiting-main', waiting);
    var badge = row.querySelector('.sw-wait');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'sw-wait';
      badge.setAttribute('aria-live', 'polite');
      row.querySelector('.sw-head').appendChild(badge);
    }
    badge.hidden = !waiting && !timedOut;
    var badgeText = waiting ? 'Menunggu jawaban main' : timedOut ? 'Jawaban main belum diterima' : '';
    if (badge.textContent !== badgeText) badge.textContent = badgeText;
    badge.title = waiting ? 'Worker menunggu balasan main sebelum melanjutkan langkah berikutnya' :
      timedOut ? 'Batas waktu menunggu habis; pertanyaan masih belum terjawab' : '';
    var state = row.querySelector('.sw-state');
    if (t.status === 'running') {
      if (!row.dataset.start && t.started) row.dataset.start = String(Math.round(t.started * 1000));
      if (!row.classList.contains('active')) row.classList.add('active');
    } else if (t.status === 'queued') {
      row.classList.remove('active');
      if (state) state.textContent = (t.depends_on || []).length ? 'waiting' : 'queued';
    } else if (t.status === 'done' || t.status === 'failed' || t.status === 'error') {
      if (!row.classList.contains('done') && !row.classList.contains('error')) {
        Tomo.swarmRowDone(row, t.status === 'done' ? 'done' : 'error');
      }
    } else {
      row.classList.remove('active');
      if (state) state.textContent = t.status === 'cancelled' ? 'stopped' : t.status;
    }
    if (TASK_ENDED[t.status] && t.status !== 'done' && t.result) {
      var live = row.querySelector('.sw-live');
      if (live && !live.textContent) { live.className = 'sw-live is-err'; live.textContent = oneLine(t.result, 180); }
    }
    var role = row.querySelector('.sw-role');
    if (role) {
      role.textContent = t.dynamic ? 'spawned' : 'roster';
      role.className = 'sw-role' + (t.dynamic ? ' is-spawned' : '');
      role.title = (t.dynamic ? 'Created for this chat only' : 'Configured agent') + (t.purpose ? ' · ' + t.purpose : '');
    }
    var deps = row.querySelector('.sw-deps');
    if (deps) {
      var pre = (t.depends_on || []).map(function (k) { return byKey[k]; }).filter(Boolean);
      if (!pre.length) { deps.hidden = true; deps.innerHTML = ''; }
      else {
        var waiting = t.status === 'queued' && pre.some(function (p) { return p.status !== 'done'; });
        deps.hidden = false;
        deps.className = 'sw-deps' + (waiting ? ' is-waiting' : '');
        deps.innerHTML = '<span>' + (waiting ? 'waiting on' : 'after') + '</span>' + pre.map(function (p) {
          return '<b class="is-' + esc(p.status) + '" style="--c:' + colorOf(null, p.agent_id) + '">' +
            esc(p.agent_name || nameOf(run, p.agent_id)) + '</b>';
        }).join('');
      }
    }
    var track = row.querySelector('.sw-track > i');
    if (track) {
      var total = sp.t1 - sp.t0;
      var a = t.started || null;
      var b = t.ended || (t.status === 'running' ? sp.t1 : null);
      if (!a || !b) { track.style.opacity = '0'; }
      else {
        track.style.opacity = '';
        track.style.left = Math.max(0, (a - sp.t0) / total * 100).toFixed(2) + '%';
        track.style.width = Math.max(1.2, (b - a) / total * 100).toFixed(2) + '%';
      }
    }
    row.classList.toggle('no-trace', !row._buffer && !row._wired);
  }

  function boardHtml(card, run, colors) {
    if (!run.board.length && !run.reviewing) return '';
    var items = run.board.slice().reverse();
    var all = card._srBoardAll;
    var shown = all ? items : items.slice(0, 3);
    return '<header><b>Shared board</b><span>' + (run.reviewing ? esc(run.coordinator) + ' reviewing…' : run.board.length) + '</span></header><ol>' +
      shown.map(function (e) {
        var c = isCoordinator(run, e.from) ? 'var(--accent)' : colors[e.from] || colorOf(null, e.from);
        var who = e.kind === 'user_update' ? 'You' : esc(nameOf(run, e.from));
        var labels = { finding: 'finding', question: 'question', coordinator_note: 'coordination',
                       coordinator_error: 'review failed', user_update: 'guidance' };
        var verb = e.to ? '<em>→ ' + esc(nameOf(run, e.to)) +
          (e.kind === 'question' ? ' · question' : '') + '</em>'
          : '<em class="is-finding">' + (labels[e.kind] || 'message') + '</em>';
        if (e.delivered && e.delivered.length) verb += '<em title="Added to worker context; action is not confirmed"> · delivered</em>';
        return '<li class="is-' + e.kind + '" style="--c:' + c + '">' +
          '<i aria-hidden="true"></i><div><p><b>' + who + '</b>' + verb +
          '<time>+' + clock(e.at - run.t0) + '</time></p><q>' + esc(oneLine(e.content, 280)) + '</q></div></li>';
      }).join('') + '</ol>' +
      (items.length > 3 ? '<button type="button" class="sr-board-more">' +
        (all ? 'Show less' : 'Show all ' + items.length) + '</button>' : '');
  }

  function paint(card) {
    var run = card._srRun;
    if (!run || !window.Tomo) return;
    var lanes = ensure(card);
    var phase = phaseOf(run), c = counts(run), sp = span(run);
    var existing = lanes.querySelectorAll('.swarm-row').length;
    run.tasks.forEach(function (t) {
      if (!t.agent_id) return;
      var row = rowFor(lanes, t);
      if (!row) {
        row = Tomo.buildSwarmRow({ key: 'd:' + t.id, aid: t.agent_id, name: t.agent_name || nameOf(run, t.agent_id),
                                   task: t.brief, historic: true });
        row.dataset.bufferKey = 'd:' + t.id;
        lanes.appendChild(row);
      } else if (row.parentNode !== lanes) {
        lanes.appendChild(row);
      }
      row.setAttribute('role', 'listitem');
    });
    var colors = assignHues(run, lanes);
    run.tasks.forEach(function (t) { var row = t.agent_id && rowFor(lanes, t); if (row) paintLane(run, t, row, sp); });
    card.classList.toggle('is-empty-plan', !run.tasks.length && !existing);
    paintHead(card, run, phase, c, colors);
    var board = card.querySelector('.sr-board');
    var html = boardHtml(card, run, colors);
    if (board._html !== html) { board.innerHTML = html; board._html = html; }
    board.hidden = !html;
  }

  function mount(card, run, opts) {
    if (!card || !run) return;
    opts = opts || {};
    card.classList.add('sr');
    buildChrome(card);
    var fresh = card._srRun !== run;
    card._srRun = run;
    if (fresh && !card._srUserToggled && opts.collapse != null) {
      card.classList.toggle('is-collapsed', !!opts.collapse);
      card.querySelector('.sr-head').setAttribute('aria-expanded', opts.collapse ? 'false' : 'true');
    }
    card.classList.toggle('is-live', !!opts.live || card.classList.contains('is-live'));
    paint(card);
    if (ENDED[run.status]) stopTick(card); else startTick(card);
  }

  function startTick(card) {
    if (card._srTick) return;
    card._srTick = setInterval(function () {
      if (!document.body.contains(card)) return stopTick(card);
      paint(card);
      if (ENDED[card._srRun.status]) stopTick(card);
    }, 1000);
  }
  function stopTick(card) {
    if (card._srTick) { clearInterval(card._srTick); card._srTick = null; }
  }

  // Stored runs → the history turns that rendered their workers.
  function hydrate(scroll, data) {
    if (!scroll || !data || !data.runs) return;
    data.runs.forEach(function (api) {
      var run = null, card = null;
      (api.tasks || []).some(function (t) {
        var key = cssKey('d:' + t.id);
        var row = scroll.querySelector('.swarm-row[data-buffer-key="' + key + '"], .swarm-row[data-instance-key="' + key + '"]');
        card = row && row.closest('.swarm-card');
        return !!card;
      });
      if (!card) return;
      run = fromApi(api, data.agents);
      mount(card, run, { collapse: !!ENDED[run.status] });
    });
  }

  window.TomoSwarm = { apply: apply, fromApi: fromApi, mount: mount, lanes: ensure, hydrate: hydrate, paint: paint };
})();

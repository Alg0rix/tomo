/* home.js — 家 Home: composer, needs, live, today, rooms, household. */
(function () {
  "use strict";
  var root = document.getElementById('homeRoot');
  if (!root) return;

  function esc(s) { return Tomo.escapeHtml(s); }
  function $(id) { return document.getElementById(id); }
  function ago(t) { return Tomo.ts ? Tomo.ts(t) : ''; }
  function clock(t) {
    var v = Number(t);
    if (!v) return '';
    return new Date(v * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  function until(t) {
    var d = Number(t) - Date.now() / 1000;
    if (!(d > 0)) return 'due';
    if (d < 3600) return 'in ' + Math.max(1, Math.round(d / 60)) + 'm';
    if (d < 86400) return 'in ' + Math.round(d / 3600) + 'h';
    return clock(t) + ' · ' + new Date(t * 1000).toLocaleDateString(undefined, { weekday: 'short' });
  }
  function initial(name) { return esc(String(name || '?').trim().slice(0, 1).toUpperCase() || '?'); }
  function avatar(id, name, cls) {
    return '<span class="home-av' + (cls ? ' ' + cls : '') + '" style="background:' + Tomo.avatarColor(String(id || name || '?')) + '">' + initial(name || id) + '</span>';
  }

  var SVG = {
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/></svg>',
    help: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/><path d="M12 17h.01"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/></svg>',
    swarm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="18" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="M12 7.5v4M10.5 12.5 6.5 16M13.5 12.5l4 3.5"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
    term: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l6-5-6-5M12 19h8"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    tg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 4 3 11l6 2 2 6 3-4 5 4z"/></svg>',
    open: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17 17 7M9 7h8v8"/></svg>',
  };
  var ICONS = {};
  try { ICONS = JSON.parse(($('homePluginIcons') || {}).textContent || '{}'); } catch (e) {}
  function pluginIcon(name) {
    var inner = ICONS[name] || ICONS.puzzle || '';
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }

  var state = { data: null, editing: false, layout: { order: [], hidden: [] }, mode: 'solo', coordinatorId: null };

  /* ---------------- composer ---------------- */
  var form = $('homeChatForm');
  var input = $('homeChatInput');
  var sendBtn = $('homeChatSend');

  function resizeInput() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }
  function syncSend() {
    // Require a message — an empty composer must not create a persisted session.
    sendBtn.disabled = !input.value.trim() || sendBtn.dataset.busy === '1';
  }
  function prefill(text) {
    input.value = text || '';
    resizeInput();
    syncSend();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    form.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  function startChat(text) {
    text = (text || '').trim();
    if (!text) return;
    sendBtn.dataset.busy = '1';
    syncSend();
    var p = new URLSearchParams();
    if (state.mode === 'swarm') p.set('swarm', '1');
    else if (state.coordinatorId) p.set('agent', state.coordinatorId);
    p.set('q', text);
    // Always pass wp (may be empty = Tomo work dir).
    p.set('wp', ($('homeWorkplace') || {}).value || '');
    window.location.href = '/sessions?' + p.toString();
  }

  input.addEventListener('input', function () { resizeInput(); syncSend(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!sendBtn.disabled) form.requestSubmit();
    }
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); startChat(input.value); });
  window.addEventListener('pageshow', function () { delete sendBtn.dataset.busy; syncSend(); });

  root.querySelectorAll('.home-seg button').forEach(function (b) {
    b.addEventListener('click', function () {
      state.mode = b.dataset.mode;
      root.querySelectorAll('.home-seg button').forEach(function (x) {
        var on = x === b;
        x.classList.toggle('on', on);
        x.setAttribute('aria-checked', on ? 'true' : 'false');
      });
      input.placeholder = state.mode === 'swarm'
        ? 'Describe a goal — the household will split it up…'
        : 'Ask ' + (($('homeCoordName') || {}).textContent || 'Tomo') + ' anything…';
    });
  });

  function fillWorkplaces(list, selectedId) {
    var sel = $('homeWorkplace');
    if (!sel) return;
    var keep = selectedId != null ? selectedId : (sel.value || '');
    var sorted = (list || []).slice().sort(function (a, b) {
      var ka = a.kind === 'local' ? 0 : 1, kb = b.kind === 'local' ? 0 : 1;
      if (ka !== kb) return ka - kb;
      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
    var opts = ['<option value="">Tomo work dir</option>'];
    sorted.forEach(function (w) {
      var path = w.kind === 'local' ? (w.root_path || '')
        : w.kind === 'ssh' ? ((w.ssh_user || '') + '@' + (w.ssh_host || '')).replace(/^@/, '')
        : (w.connector_hostname || w.host_detail || '') + (w.online ? ' · online' : ' · offline');
      opts.push('<option value="' + esc(w.id) + '" title="' + esc(path || w.name || w.id) + '">' +
        esc(w.name || w.id) + ' · ' + esc(w.kind || '?') + '</option>');
    });
    sel.innerHTML = opts.join('');
    sel.value = keep && sorted.some(function (w) { return w.id === keep; }) ? keep : '';
  }
  async function loadWorkplaces(selectedId) {
    try {
      var d = await Tomo.api('/api/workplaces');
      fillWorkplaces((d && d.workplaces) || [], selectedId);
    } catch (e) {}
  }
  $('homeBrowseFolder').addEventListener('click', function () {
    if (!Tomo.pickLocalFolder) { Tomo.toast('Folder picker not loaded', 'err'); return; }
    Tomo.pickLocalFolder({ title: 'Open folder for new chat' }).then(async function (res) {
      await loadWorkplaces(res.workplace_id);
      Tomo.toast((res.created ? 'Registered ' : 'Using ') + (res.path || res.workplace_id), 'ok');
    }).catch(function (err) {
      if (err && err.message === 'cancelled') return;
      Tomo.toast((err && err.message) || 'Browse failed', 'err');
    });
  });

  /* ---------------- starters ---------------- */
  var coreStarters = [];
  function renderStarters() {
    var box = $('homeStarters');
    var plugin = (state.data && state.data.starters) || [];
    var all = coreStarters.slice(0, 4).concat(plugin.slice(0, 6));
    if (!all.length) { box.innerHTML = ''; return; }
    box.innerHTML = '<span class="lbl">Try</span>' + all.map(function (s, i) {
      return '<button type="button" class="home-starter" data-i="' + i + '">' + esc(s.label) +
        (s.source ? '<span class="src">' + esc(s.source) + '</span>' : '') + '</button>';
    }).join('');
    box.querySelectorAll('.home-starter').forEach(function (b) {
      b.addEventListener('click', function () { prefill(all[Number(b.dataset.i)].prompt); });
    });
  }
  async function loadPrompts() {
    try {
      var d = await Tomo.api('/api/dashboard/prompts');
      coreStarters = ((d && d.prompts) || []).filter(function (p) { return p && p.label && p.prompt; });
    } catch (e) { coreStarters = []; }
    renderStarters();
  }

  /* ---------------- header + foundations ---------------- */
  function renderHeader(d) {
    var h = new Date().getHours();
    var part = h < 5 ? 'Late night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
    var name = root.dataset.username;
    $('homeGreeting').textContent = part + (name ? ', ' + name : '');
    var busy = (d.household || []).filter(function (a) { return a.state === 'busy'; }).length;
    var bits = [];
    var n = (d.needs || []).length;
    if (n) bits.push('<b>' + n + (n === 1 ? ' thing needs' : ' things need') + ' you</b>');
    bits.push(busy ? busy + (busy === 1 ? ' resident is' : ' residents are') + ' working' : 'The house is quiet');
    var rooms = (d.rooms || []).filter(function (r) { return r.plugin; }).length;
    if (rooms) bits.push(rooms + (rooms === 1 ? ' room' : ' rooms') + ' open');
    $('homeSummary').innerHTML = bits.join(' · ');
  }
  function renderFoundations(list) {
    $('homeFoundations').innerHTML = (list || []).map(function (f) {
      return '<a class="home-pill" href="' + esc(f.href) + '"><i class="' + esc(f.state) + '"></i>' +
        esc(f.label) + ' <b class="mono">' + esc(f.value) + '</b></a>';
    }).join('');
  }
  function renderCoordinator(c) {
    if (!c) return;
    state.coordinatorId = c.id;
    $('homeCoordName').textContent = c.name || c.id;
    var av = $('homeCoordAv');
    av.textContent = String(c.name || c.id).slice(0, 1).toUpperCase();
    av.style.background = Tomo.avatarColor(c.id);
    if (state.mode === 'solo') input.placeholder = 'Ask ' + (c.name || 'Tomo') + ' anything…';
  }

  /* ---------------- 待 needs ---------------- */
  var resolving = {};
  function needCard(n) {
    var chat = n.chat || {};
    var chatLink = '<a href="/sessions?s=' + encodeURIComponent(chat.id || '') + '">' + esc(Tomo.truncate(chat.title || 'Conversation', 48)) + '</a>';
    var meta = chatLink + (chat.channel && chat.channel !== 'web' ? ' · via ' + esc(chat.channel) : '') + ' · ' + esc(ago(n.at));
    var cls = 'home-need', ico = SVG.shield, body = '', acts = '';
    if (n.kind === 'approval') {
      if (n.risk) cls += ' danger';
      body = '<div class="t">' + esc(n.tool ? 'Run ' + n.tool : 'Approve action') + (n.title ? ' <code>' + esc(Tomo.truncate(n.title, 90)) + '</code>' : '') + '</div>' +
        '<div class="m">' + (n.description ? esc(Tomo.truncate(n.description, 120)) + ' · ' : '') + meta + '</div>';
      if ((n.choices || []).indexOf('deny') !== -1) acts += '<button type="button" class="btn ghost sm" data-act="deny">Deny</button>';
      if ((n.choices || []).indexOf('once') !== -1) acts += '<button type="button" class="btn primary sm" data-act="once">Approve</button>';
      if (!acts) acts = '<a class="btn ghost sm" href="/sessions?s=' + encodeURIComponent(chat.id || '') + '">Open chat</a>';
    } else if (n.kind === 'question') {
      cls += ' info'; ico = SVG.help;
      body = '<div class="t">' + esc(n.title) + '</div><div class="m">' + meta + '</div>';
      acts = (n.choices || []).map(function (c, i) {
        return '<button type="button" class="btn ' + (i ? 'ghost' : 'primary') + ' sm" data-answer="' + esc(c) + '">' + esc(c) + '</button>';
      }).join('') || '<a class="btn primary sm" href="/sessions?s=' + encodeURIComponent(chat.id || '') + '">Answer</a>';
    } else {
      cls += ' info'; ico = SVG.key;
      body = '<div class="t">' + esc(n.title) + '</div><div class="m">' + meta + '</div>';
      acts = '<a class="btn primary sm" href="/sessions?s=' + encodeURIComponent(chat.id || '') + '">Provide</a>';
    }
    return '<div class="' + cls + '" data-kind="' + esc(n.kind) + '" data-id="' + esc(n.id) + '">' +
      '<div class="ico">' + ico + '</div><div>' + body + '</div><div class="acts">' + acts + '</div>' +
      '<div class="home-stamp" aria-hidden="true">承認</div></div>';
  }
  function renderNeeds(list) {
    list = (list || []).filter(function (n) { return !resolving[n.id]; });
    if (Tomo.setRailBadges) Tomo.setRailBadges({ needs: list.length });
    $('needsCount').textContent = list.length ? list.length + ' waiting' : '';
    var box = $('homeNeeds');
    if (!list.length) {
      box.innerHTML = '<div class="home-clear"><span class="kanji" aria-hidden="true">済</span>Nothing waiting on you.</div>';
      return;
    }
    box.innerHTML = list.map(needCard).join('');
  }
  function dismissNeed(el, stamp) {
    if (stamp) el.classList.add('stamped');
    setTimeout(function () {
      el.classList.add('gone');
      setTimeout(function () {
        el.remove();
        if (!$('homeNeeds').querySelector('.home-need')) renderNeeds([]);
        else {
          var left = $('homeNeeds').querySelectorAll('.home-need').length;
          $('needsCount').textContent = left + ' waiting';
          if (Tomo.setRailBadges) Tomo.setRailBadges({ needs: left });
        }
      }, 400);
    }, stamp ? 650 : 0);
  }
  $('homeNeeds').addEventListener('click', async function (e) {
    var btn = e.target.closest('button[data-act],button[data-answer]');
    if (!btn) return;
    var card = btn.closest('.home-need');
    var id = card.dataset.id;
    card.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    resolving[id] = true;
    try {
      if (btn.dataset.act) {
        await Tomo.api('/api/approvals/' + encodeURIComponent(id), {
          method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ choice: btn.dataset.act }),
        });
        dismissNeed(card, btn.dataset.act === 'once');
      } else {
        await Tomo.api('/api/clarify/' + encodeURIComponent(id), {
          method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ answer: btn.dataset.answer }),
        });
        dismissNeed(card, false);
      }
      setTimeout(function () { delete resolving[id]; }, 15000);
    } catch (err) {
      delete resolving[id];
      card.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
      Tomo.toast((err && err.message) || 'Could not resolve', 'err');
      refreshLive();
    }
  });

  /* ---------------- 今 live ---------------- */
  function laneClass(s) {
    s = String(s || '');
    if (/^(done|completed|succeeded|ok)$/.test(s)) return 'done';
    if (/^(failed|error|cancelled|interrupted|timeout)$/.test(s)) return 'failed';
    if (/^(running|in_progress|working|started)$/.test(s)) return 'running';
    return '';
  }
  function renderLive(list) {
    var box = $('homeLive');
    if (!list || !list.length) {
      box.innerHTML = '<div class="home-empty">Nothing running. Start a chat or a swarm above.</div>';
      return;
    }
    box.innerHTML = list.map(function (it) {
      var ico = SVG.chat, tag = '<span class="home-tag run">live</span>', right = esc(ago(it.since));
      var extra = '';
      if (it.kind === 'swarm') {
        ico = SVG.swarm; tag = '<span class="home-tag run">swarm</span>';
        if (it.lanes && it.lanes.length) {
          extra = '<div class="home-lanes" aria-hidden="true">' + it.lanes.map(function (s) {
            return '<span class="home-lane ' + laneClass(s) + '" title="' + esc(s) + '"></span>';
          }).join('') + '</div>';
        }
      } else if (it.kind === 'process') {
        ico = SVG.term; tag = '<span class="home-tag">' + esc(it.status || 'process') + '</span>';
      } else if (it.kind === 'routine') {
        ico = SVG.clock; tag = '<span class="home-tag info">routine</span>'; right = esc(until(it.at));
      }
      var detail = it.kind === 'swarm' && it.crew && it.crew.length ? it.detail + ' · ' + it.crew.join(', ') : it.detail;
      return '<a class="home-lrow" href="' + esc(it.href || '#') + '"><span class="ico">' + ico + '</span>' +
        '<div><div class="t"><span>' + esc(it.title) + '</span>' + tag + '</div>' +
        (detail ? '<div class="m">' + esc(detail) + '</div>' : '') + extra + '</div>' +
        '<span class="home-r">' + right + '</span></a>';
    }).join('');
  }

  /* ---------------- 日 today ---------------- */
  function renderToday(t) {
    t = t || { items: [], upcoming: [] };
    if (t.date) {
      var parts = t.date.split('-').map(Number);
      $('todayDate').textContent = new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
    }
    var items = (t.items || []).slice().sort(function (a, b) { return a.at - b.at; });
    var box = $('homeToday');
    if (!items.length && !(t.upcoming || []).length) {
      box.innerHTML = '<div class="home-empty">A fresh day. What Tomo does and learns will show up here.</div>';
      return;
    }
    /* journal text is raw markdown; strip the noise so rows stay one or two lines */
    function plain(t) { return String(t || '').replace(/[*`_#>|]+/g, ' ').replace(/-{3,}/g, ' ').replace(/\s+/g, ' ').trim(); }
    function row(it, future) {
      var cls = 'home-ti' + (future ? ' future' : '');
      var label = '';
      if (it.kind === 'learned') { cls += ' learned'; label = 'Learned'; }
      else if (it.kind === 'routine') { cls += it.status === 'error' ? ' err' : ' done'; label = future ? 'Routine' : 'Ran'; }
      else { cls += ' done'; }
      var x = (label ? '<b>' + esc(label) + '</b> · ' : '') + esc(plain(it.title)) + (it.meta ? ' <small>· ' + esc(it.meta) + '</small>' : '');
      return '<a class="' + cls + '" href="' + esc(it.href || '#') + '"><div class="h">' + esc(clock(it.at)) + '</div><div class="x">' + x + '</div></a>';
    }
    box.innerHTML = '<div class="home-tl">' + items.map(function (it) { return row(it, false); }).join('') +
      '<div class="home-ti now">NOW · ' + esc(clock(Date.now() / 1000)) + '</div>' +
      (t.upcoming || []).map(function (it) { return row(it, true); }).join('') + '</div>';
    var now = box.querySelector('.now');
    if (now && box.scrollHeight > box.clientHeight) box.scrollTop = Math.max(0, now.offsetTop - box.clientHeight * 0.6);
  }

  /* ---------------- 話 recent ---------------- */
  function renderRecent(list) {
    var box = $('homeRecent');
    if (!list || !list.length) {
      box.innerHTML = '<div class="home-empty">No chats yet. Say hi above.</div>';
      return;
    }
    box.innerHTML = list.map(function (c) {
      var ico = c.via === 'swarm' ? SVG.swarm : c.via === 'telegram' ? SVG.tg : SVG.chat;
      return '<a class="home-cr' + (c.active ? ' active' : '') + '" href="/sessions?s=' + encodeURIComponent(c.id) + '">' + ico +
        '<span class="t">' + esc(c.title) + '</span><span class="via">' + esc(c.via) + '</span>' +
        '<span class="home-r">' + (c.active ? '<span class="home-pulse" title="working"></span>' : esc(ago(c.updated_at))) + '</span></a>';
    }).join('');
  }

  /* ---------------- 族 household ---------------- */
  function renderHouse(list) {
    var box = $('homeHouse');
    if (!list || !list.length) {
      box.innerHTML = '<div class="home-empty">No residents yet. <a href="/agents">Add an agent ↗</a></div>';
      return;
    }
    box.innerHTML = list.map(function (a) {
      return '<a class="home-res ' + esc(a.state) + '" href="/agents/' + encodeURIComponent(a.id) + '">' + avatar(a.id, a.name) +
        '<div><div class="n">' + esc(a.name) + '</div><div class="m">' + esc(a.activity || a.state) + '</div></div></a>';
    }).join('');
  }

  /* ---------------- 間 rooms ---------------- */
  function sparkSvg(values) {
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = max - min || 1, w = 240, h = 46;
    var pts = values.map(function (v, i) {
      return (i * w / (values.length - 1)).toFixed(1) + ',' + (h - 4 - (v - min) / span * (h - 8)).toFixed(1);
    });
    return '<svg class="home-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<polygon points="0,' + h + ' ' + pts.join(' ') + ' ' + w + ',' + h + '" fill="var(--accent-soft)"/>' +
      '<polyline points="' + pts.join(' ') + '" fill="none" stroke="var(--accent)" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>';
  }
  function trendHtml(t) {
    if (!t) return '';
    var arrow = t.direction === 'down' ? '↓ ' : t.direction === 'up' ? '↑ ' : '';
    return '<span class="' + (t.good ? 'good' : 'bad') + '">' + arrow + esc(t.text) + '</span>';
  }
  function whoHtml(w, bare) {
    if (!w) return '';
    var bg = w.agent ? ' style="background:' + Tomo.avatarColor(w.agent) + '"' : '';
    var av = '<span class="home-av xs' + (w.agent ? '' : ' you') + '"' + bg + (bare ? ' title="' + esc(w.name) + '"' : '') + '>' + initial(w.name) + '</span>';
    return bare ? av : '<span class="home-who">' + av + esc(w.name) + '</span>';
  }
  var TONE_VAR = { hot: 'var(--danger)', warm: 'var(--accent)', ok: 'var(--ok)', info: 'var(--info)' };
  var SERIES = ['var(--accent)', 'var(--info)', 'var(--ok)', 'var(--text-dim)', 'var(--danger)', 'var(--text-faint)'];
  function chartHtml(points) {
    var max = Math.max.apply(null, points.map(function (p) { return p.value; })) || 1;
    var every = points.length <= 8 ? 1 : Math.ceil(points.length / 6);
    return '<div class="home-chart" style="--n:' + points.length + '">' + points.map(function (p, i) {
      var label = (i % every === 0 || i === points.length - 1) ? esc(p.label) : '';
      return '<div class="c ' + esc(p.tone || '') + '" title="' + esc(p.label) + ': ' + esc(p.value) + '">' +
        '<b style="height:' + Math.max(2, Math.round(p.value / max * 100)) + '%"></b><span>' + label + '</span></div>';
    }).join('') + '</div>';
  }
  function ringHtml(r) {
    var total = r.segments.reduce(function (a, s) { return a + s.value; }, 0) || 1;
    var c = 2 * Math.PI * 15.5, offset = 0;
    var arcs = r.segments.map(function (seg, i) {
      var len = seg.value / total * c;
      var color = TONE_VAR[seg.tone] || SERIES[i % SERIES.length];
      var arc = '<circle r="15.5" cx="18" cy="18" fill="none" stroke="' + color + '" stroke-width="4.5" stroke-dasharray="' +
        len.toFixed(2) + ' ' + (c - len).toFixed(2) + '" stroke-dashoffset="' + (-offset).toFixed(2) + '"/>';
      offset += len;
      seg._color = color;
      return arc;
    }).join('');
    return '<div class="home-ring"><div class="dial"><svg viewBox="0 0 36 36" aria-hidden="true"><g transform="rotate(-90 18 18)">' +
      '<circle r="15.5" cx="18" cy="18" fill="none" stroke="var(--surface-3)" stroke-width="4.5"/>' + arcs + '</g></svg>' +
      (r.value ? '<div class="mid"><b>' + esc(r.value) + '</b>' + (r.label ? '<small>' + esc(r.label) + '</small>' : '') + '</div>' : '') + '</div>' +
      '<ul class="legend">' + r.segments.map(function (seg) {
        return '<li><i style="background:' + seg._color + '"></i><span>' + esc(seg.label) + '</span><span>' + esc(seg.text || '') + '</span></li>';
      }).join('') + '</ul></div>';
  }
  function heatHtml(h) {
    var max = Math.max.apply(null, h.values) || 1;
    var cells = h.values.map(function (v) {
      var lvl = v <= 0 ? 0 : Math.min(4, Math.ceil(v / max * 4));
      return '<i class="l' + lvl + '" title="' + esc(v) + '"></i>';
    }).join('');
    return '<div class="home-heat"><div class="g">' + cells + '</div>' + (h.label ? '<div class="cap">' + esc(h.label) + '</div>' : '') + '</div>';
  }
  function seriesHtml(s) {
    var all = [];
    s.lines.forEach(function (l) { all = all.concat(l.values); });
    var min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    var span = max - min || 1, w = 240, h = 64;
    var axis = '';
    if (min < 0 && max > 0) {
      var y = (h - 4 - (0 - min) / span * (h - 8)).toFixed(1);
      axis = '<line x1="0" y1="' + y + '" x2="' + w + '" y2="' + y + '" stroke="var(--border-strong)" stroke-width="1"/>';
    }
    var polys = s.lines.map(function (l, i) {
      var n = l.values.length;
      var color = TONE_VAR[l.tone] || SERIES[i % SERIES.length];
      l._color = color;
      var pts = l.values.map(function (v, j) {
        return (j * w / (n - 1)).toFixed(1) + ',' + (h - 4 - (v - min) / span * (h - 8)).toFixed(1);
      });
      return '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + color + '" stroke-width="1.6" vector-effect="non-scaling-stroke"/>';
    }).join('');
    var out = '<svg class="home-series" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' + axis + polys + '</svg>';
    var bits = [];
    s.lines.forEach(function (l) {
      if (l.label) bits.push('<span><i style="background:' + l._color + '"></i>' + esc(l.label) + '</span>');
    });
    if (s.labels && s.labels.length > 1) bits.push('<span class="sl">' + esc(s.labels[0]) + ' → ' + esc(s.labels[s.labels.length - 1]) + '</span>');
    if (bits.length) out += '<div class="home-slegend">' + bits.join('') + '</div>';
    return out;
  }
  function gaugeHtml(g) {
    var len = Math.PI * 40, pct = Math.max(0, Math.min(1, g.value || 0));
    var arc = 'M10 50 A40 40 0 0 1 90 50';
    var color = TONE_VAR[g.tone] || 'var(--accent)';
    return '<div class="home-gauge"><div class="dial"><svg viewBox="0 0 100 58" aria-hidden="true">' +
      '<path d="' + arc + '" fill="none" stroke="var(--surface-3)" stroke-width="8" stroke-linecap="round"/>' +
      '<path d="' + arc + '" fill="none" stroke="' + color + '" stroke-width="8" stroke-linecap="round" stroke-dasharray="' + (pct * len).toFixed(1) + ' ' + len.toFixed(1) + '"/></svg>' +
      (g.text ? '<b class="gv">' + esc(g.text) + '</b>' : '') + '</div>' +
      (g.label ? '<div class="gl">' + esc(g.label) + '</div>' : '') + '</div>';
  }
  function cardBody(d) {
    var html = '';
    var rich = ['metric', 'caption', 'stats', 'chart', 'ring', 'heatmap', 'spark', 'bars', 'columns', 'timeline', 'checklist', 'list', 'tags', 'quote', 'meter', 'notice', 'image', 'steps', 'table', 'code', 'foot', 'series', 'gauge', 'states'];
    if (d.empty && !rich.some(function (k) { return d[k]; })) {
      return '<div class="empty">' + esc(d.empty) + '</div>' + actionsHtml(d);
    }
    if (d.notice) html += '<div class="home-notice ' + esc(d.notice.tone || '') + '">' + esc(d.notice.text) + '</div>';
    if (d.metric) html += '<div class="home-metric' + (d.metric.tone ? ' ' + esc(d.metric.tone) : '') + '">' + esc(d.metric.value) + (d.metric.label ? '<small>' + esc(d.metric.label) + '</small>' : '') + '</div>';
    if (d.caption || d.trend) {
      var trend = trendHtml(d.trend);
      html += '<div class="home-cap">' + trend + (trend && d.caption ? ' · ' : '') + esc(d.caption || '') + '</div>';
    }
    if (d.image) html += '<img class="home-img" src="' + esc(d.image.src) + '" alt="' + esc(d.image.alt || '') + '" loading="lazy">';
    if (d.stats) html += '<div class="home-stats" style="--n:' + d.stats.length + '">' + d.stats.map(function (st) {
      return '<div class="st' + (st.tone ? ' ' + esc(st.tone) : '') + '"><span class="v">' + esc(st.value) + '</span><span class="k">' + esc(st.label) + '</span>' +
        (st.trend ? '<span class="t">' + trendHtml(st.trend) + '</span>' : '') + '</div>';
    }).join('') + '</div>';
    if (d.chart) html += chartHtml(d.chart);
    if (d.spark) html += sparkSvg(d.spark);
    if (d.series) html += seriesHtml(d.series);
    if (d.ring) html += ringHtml(d.ring);
    if (d.gauge) html += gaugeHtml(d.gauge);
    if (d.heatmap) html += heatHtml(d.heatmap);
    if (d.bars) html += '<div class="home-bars">' + d.bars.map(function (b) {
      return '<div class="home-bar ' + esc(b.tone || '') + '"><div class="l"><span>' + esc(b.label) + '</span><span>' + esc(b.text || '') + '</span></div>' +
        '<div class="tr"><b style="width:' + Math.round(b.value * 100) + '%"></b></div></div>';
    }).join('') + '</div>';
    if (d.steps) html += '<ol class="home-steps">' + d.steps.map(function (s) {
      return '<li class="' + esc(s.state || '') + '"><i></i><span>' + esc(s.label) + '</span></li>';
    }).join('') + '</ol>';
    if (d.states) html += '<div class="home-states">' + d.states.map(function (s) {
      return '<i class="' + esc(s.tone || '') + '" style="flex-grow:' + s.value + '"' + (s.text ? ' title="' + esc(s.text) + '"' : '') + '></i>';
    }).join('') + '</div>';
    if (d.columns) html += '<div class="home-kcols" style="--cols:' + d.columns.length + '">' + d.columns.map(function (c) {
      return '<div class="home-kcol' + (c.muted ? ' muted' : '') + '"><div class="kh"><span>' + esc(c.title) + '</span><span>' + esc(c.count) + '</span></div>' +
        (c.items || []).map(function (it) {
          var foot = whoHtml(it.who) + (it.meta ? '<span>' + (it.who ? '· ' : '') + esc(it.meta) + '</span>' : '');
          return '<div class="home-kcard ' + esc(it.tone || '') + '">' + esc(it.title) + (foot ? '<div class="who">' + foot + '</div>' : '') + '</div>';
        }).join('') + '</div>';
    }).join('') + '</div>';
    if (d.timeline) html += '<ol class="home-agenda">' + d.timeline.map(function (ev) {
      var inner = '<span class="t">' + esc(ev.time || '') + '</span><span class="dot ' + esc(ev.tone || '') + '"></span>' +
        '<span class="b"><span class="n">' + esc(ev.label) + '</span>' + (ev.meta ? '<span class="m">' + esc(ev.meta) + '</span>' : '') + '</span>';
      return '<li>' + (ev.href ? '<a href="' + esc(ev.href) + '">' + inner + '</a>' : '<div>' + inner + '</div>') + '</li>';
    }).join('') + '</ol>';
    if (d.checklist) html += '<ul class="home-check">' + d.checklist.map(function (c) {
      return '<li class="' + (c.done ? 'done' : '') + '"><span class="box" aria-hidden="true">' + (c.done ? '✓' : '') + '</span>' +
        '<span>' + esc(c.label) + '</span><span class="sr-only">' + (c.done ? '(done)' : '(open)') + '</span></li>';
    }).join('') + '</ul>';
    if (d.list) html += '<div class="home-list">' + d.list.map(function (r) {
      var lead = r.who ? whoHtml(r.who, true) : r.tone ? '<span class="dot ' + esc(r.tone) + '"></span>' : '';
      var inner = '<span class="lb">' + lead + '<span class="tx"><span>' + esc(r.label) + '</span>' + (r.sub ? '<small>' + esc(r.sub) + '</small>' : '') + '</span></span>' +
        '<span>' + esc(r.value || '') + '</span>';
      return r.href ? '<a class="li" href="' + esc(r.href) + '">' + inner + '</a>' : '<div class="li">' + inner + '</div>';
    }).join('') + '</div>';
    if (d.table) html += '<table class="home-tbl"><thead><tr>' + d.table.columns.map(function (c) {
      return '<th>' + esc(c) + '</th>';
    }).join('') + '</tr></thead><tbody>' + d.table.rows.map(function (r) {
      return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>';
    }).join('') + '</tbody></table>';
    if (d.tags) html += '<div class="home-tags">' + d.tags.map(function (t) {
      return '<span class="' + esc(t.tone || '') + '">' + esc(t.label) + '</span>';
    }).join('') + '</div>';
    if (d.quote) html += '<p class="home-quote">' + esc(d.quote) + '</p>';
    if (d.code) html += '<pre class="home-code">' + esc(d.code) + '</pre>';
    if (d.meter) {
      var filled = Math.round(d.meter.value * 10);
      var cells = '';
      for (var i = 0; i < 10; i++) cells += '<i' + (i < filled ? ' class="f"' : '') + '></i>';
      html += '<div class="home-meter"><div class="l"><span>' + esc(d.meter.label || '') + '</span><span>' + esc(d.meter.text || '') + '</span></div><div class="home-bond">' + cells + '</div></div>';
    }
    if (d.foot) html += '<div class="home-foot">' + esc(d.foot) + '</div>';
    return html + actionsHtml(d);
  }
  function actionsHtml(d) {
    if (!d.actions) return '';
    return '<div class="home-ractions">' + d.actions.map(function (a, i) {
      return a.prompt
        ? '<button type="button" class="home-ra ask" data-prompt-i="' + i + '">' + esc(a.label) + '</button>'
        : '<a class="home-ra" href="' + esc(a.href) + '">' + esc(a.label) + '</a>';
    }).join('') + '</div>';
  }
  var CORE_KANJI = { memory: '記', companion: '友' };
  function isSelected(r) {
    if (Array.isArray(state.layout.selected)) return state.layout.selected.indexOf(r.key) !== -1;
    return r.default_visible !== false && state.layout.hidden.indexOf(r.key) === -1;
  }
  function selectWidget(key, selected) {
    if (!Array.isArray(state.layout.selected)) state.layout.selected = state.data.rooms.filter(isSelected).map(function (r) { return r.key; });
    state.layout.selected = state.layout.selected.filter(function (k) { return k !== key; });
    if (selected) state.layout.selected.push(key);
    renderRooms(); renderPicker(); saveLayout();
  }
  var WIDTHS = [['s', 'Small'], ['m', 'Medium'], ['l', 'Full']];
  var HEIGHTS = [null, 240, 360, 480];
  function heightLabel(h) { return h ? h + 'px' : 'Auto'; }
  function setSize(key, width, height) {
    if (!state.layout.sizes) state.layout.sizes = {};
    state.layout.sizes[key] = { width: width, height: height };
  }
  /* one place that applies a size to state, DOM and controls */
  function applySize(el, width, height) {
    setSize(el.dataset.key, width, height);
    el.classList.remove('s', 'm', 'l'); el.classList.add(width);
    el.classList.toggle('fixed', !!height);
    el.style.height = height ? height + 'px' : '';
    el.querySelectorAll('[data-width]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.width === width)); });
    el.querySelector('[data-height-cycle]').textContent = heightLabel(height);
  }
  function sizeOf(el) {
    var r = state.data.rooms.filter(function (x) { return x.key === el.dataset.key; })[0];
    return widgetSize(r);
  }
  function widgetSize(r) {
    return (state.layout.sizes || {})[r.key] || { width: r.size || 's', height: null };
  }
  function roomIco(r) {
    var kanji = r.core ? (CORE_KANJI[r.core] || '家') : r.kanji;
    return kanji
      ? '<span class="home-room-ico kanji" aria-hidden="true">' + esc(kanji) + '</span>'
      : '<span class="home-room-ico">' + pluginIcon(r.icon) + '</span>';
  }
  var ADD_TILE = '<button type="button" class="home-room home-addtile" id="homeAddTile"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><b>Add widget</b><span>from your plugins</span></button>';
  function roomStatus(r) {
    if (r.stale) return '<span class="home-status warm" title="' + esc(r.stale) + '">Stale</span>';
    return r.data && r.data.status
      ? '<span class="home-status ' + esc(r.data.status.tone || '') + '">' + esc(r.data.status.text) + '</span>' : '';
  }
  function roomBody(r) {
    return r.error ? '<div class="err">' + esc(r.error) + '</div>' : cardBody(r.data || {});
  }
  function updateRoom(r) {
    var el = $('homeRooms').querySelector('[data-key="' + CSS.escape(r.key) + '"]');
    if (!el || state.editing) return;
    var status = el.querySelector('.home-status');
    if (status) status.remove();
    el.querySelector('.home-room-h .open').insertAdjacentHTML('beforebegin', roomStatus(r));
    // Keep focused actions intact until focus leaves the card.
    if (!el.contains(document.activeElement)) el.querySelector('.home-widget-body').innerHTML = roomBody(r);
  }
  function roomHtml(r) {
    var size = widgetSize(r);
    var ico = roomIco(r);
    var status = roomStatus(r);
    var body = roomBody(r);
    var t = esc(r.title);
    return '<div class="home-room ' + esc(size.width) + (size.height ? ' fixed' : '') + '"' + (size.height ? ' style="height:' + size.height + 'px"' : '') + ' data-key="' + esc(r.key) + '">' +
      '<div class="home-handle" role="toolbar" aria-label="Arrange ' + t + '">' +
      '<button type="button" class="home-hb grip" data-drag aria-label="Drag ' + t + ' to reorder" title="Drag to reorder">⠿</button>' +
      '<button type="button" class="home-hb" data-move="-1" aria-label="Move ' + t + ' earlier" title="Move earlier">↑</button>' +
      '<button type="button" class="home-hb" data-move="1" aria-label="Move ' + t + ' later" title="Move later">↓</button>' +
      '<span class="home-seg" role="group" aria-label="Width of ' + t + '">' +
      WIDTHS.map(function (w) { return '<button type="button" class="home-hb" data-width="' + w[0] + '" aria-pressed="' + (w[0] === size.width) + '" aria-label="' + w[1] + ' width" title="' + w[1] + ' width">' + w[0].toUpperCase() + '</button>'; }).join('') + '</span>' +
      '<button type="button" class="home-hb txt" data-height-cycle aria-label="Height of ' + t + '" title="Change height">' + heightLabel(size.height) + '</button>' +
      '<button type="button" class="home-hb danger" data-remove aria-label="Remove ' + t + '" title="Remove from Home">×</button></div>' +
      '<div class="home-room-h">' + ico + '<span class="n">' + esc(r.title) + '</span>' +
      '<span class="src">' + (r.core ? 'core' : 'plugin') + '</span>' + status +
      '<a class="open" href="' + esc(r.href || '#') + '" aria-label="Open ' + esc(r.title) + '">' + SVG.open + '</a></div>' +
      '<div class="home-widget-body">' + body + '</div><button type="button" class="home-resize" data-resize aria-label="Resize ' + t + ' (arrow keys change size, Delete resets height)" title="Drag to resize">◢</button></div>';
  }
  function orderedRooms() {
    var rooms = (state.data && state.data.rooms) || [];
    var pos = {};
    state.layout.order.forEach(function (k, i) { pos[k] = i; });
    return rooms.map(function (r, i) { return { r: r, i: i }; }).sort(function (a, b) {
      var pa = a.r.key in pos ? pos[a.r.key] : 1000 + a.i;
      var pb = b.r.key in pos ? pos[b.r.key] : 1000 + b.i;
      return pa - pb;
    }).map(function (x) { return x.r; });
  }
  function renderRooms() {
    var box = $('homeRooms');
    var rooms = orderedRooms().filter(isSelected);
    box.innerHTML = rooms.map(roomHtml).join('') + ADD_TILE;
  }

  /* arrange mode */
  function currentOrder() {
    return Array.prototype.map.call($('homeRooms').querySelectorAll('.home-room[data-key]'), function (el) { return el.dataset.key; });
  }
  var saveTimer = null;
  var saving = Promise.resolve();
  var msgTimer = null;
  /* status line in the arrange bar: saved/undo feedback, announced politely */
  function say(text, undo) {
    var box = $('homeEditMsg');
    clearTimeout(msgTimer);
    box.textContent = text;
    if (undo) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'home-undo'; b.textContent = 'Undo';
      b.addEventListener('click', function () { undo(); say(''); });
      box.appendChild(b);
    }
    if (text && !undo) msgTimer = setTimeout(function () { box.textContent = ''; }, 2500);
  }
  function saveLayout() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var body = JSON.stringify(state.layout);
      saving = saving.then(async function () {
        try {
          await Tomo.api('/api/home/layout', {
            method: 'PUT', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: body,
          });
          if (!$('homeEditMsg').querySelector('.home-undo')) say('Saved ✓');
        } catch (e) { say('Not saved'); Tomo.toast('Could not save layout', 'err'); }
      });
    }, 300);
  }
  function setEditing(on) {
    state.editing = on;
    root.classList.toggle('editing', on);
    $('homeEditBar').hidden = !on;
    $('homeArrange').setAttribute('aria-pressed', on ? 'true' : 'false');
    if (!on) {
      say('');
      (state.data && state.data.rooms || []).filter(isSelected).forEach(updateRoom);
      refreshCards();
    }
  }
  $('homeArrange').addEventListener('click', function () { setEditing(!state.editing); });
  $('homeEditDone').addEventListener('click', function () { setEditing(false); $('homeArrange').focus(); });
  var resetTimer = null;
  $('homeResetLayout').addEventListener('click', function () {
    var btn = this;
    if (!btn.dataset.armed) {
      btn.dataset.armed = '1'; btn.textContent = 'Reset everything?';
      resetTimer = setTimeout(disarm, 3500);
      return;
    }
    disarm();
    var before = JSON.parse(JSON.stringify(state.layout));
    state.layout = { order: [], hidden: [] };
    renderRooms();
    saveLayout();
    say('Layout reset', function () { state.layout = before; renderRooms(); saveLayout(); });
  });
  function disarm() {
    var btn = $('homeResetLayout');
    clearTimeout(resetTimer); delete btn.dataset.armed; btn.textContent = 'Reset';
  }
  var roomsBox = $('homeRooms');
  roomsBox.addEventListener('click', function (e) {
    var button = e.target.closest('[data-prompt-i]');
    if (!button) return;
    var el = button.closest('[data-key]');
    var room = state.data.rooms.find(function (r) { return r.key === el.dataset.key; });
    var action = room && room.data && room.data.actions && room.data.actions[Number(button.dataset.promptI)];
    if (action && action.prompt) prefill(action.prompt);
  });
  var justAdded = [];
  function renderPicker() {
    var groups = Object.create(null), total = 0;
    orderedRooms().forEach(function (r) {
      var group = r.plugin_name || (r.plugin || 'Tomo').replace(/[_-]+/g, ' ').replace(/\b[a-z]/g, function (c) { return c.toUpperCase(); });
      (groups[group] || (groups[group] = [])).push(r);
    });
    $('homeWidgetChoices').innerHTML = Object.keys(groups).map(function (name) {
      return '<section><h3>' + esc(name) + '</h3>' + groups[name].map(function (r) {
        var added = isSelected(r);
        if (added) total++;
        var w = widgetSize(r).width;
        return '<div class="home-widget-choice' + (added ? ' on' : '') + '">' + roomIco(r) +
          '<div class="m"><div class="t">' + esc(r.title) + '</div><div class="d">' + esc(name) + ' · ' + (w === 'l' ? 'Full' : w === 'm' ? 'Medium' : 'Small') + '</div></div>' +
          '<button type="button" class="btn sm ' + (added ? 'ghost' : 'primary') + '" data-add-widget="' + esc(r.key) + '" aria-label="' + (added ? 'Remove ' : 'Add ') + esc(r.title) + '">' + (added ? '✓ Added' : 'Add') + '</button></div>';
      }).join('') + '</section>';
    }).join('') || '<p>No widgets available. Enable a plugin to add its widgets.</p>';
    $('homePickerCount').textContent = total + (total === 1 ? ' widget' : ' widgets') + ' on Home · from enabled plugins';
  }
  function openPicker() {
    if (!state.data) return;
    justAdded = []; renderPicker(); $('homeWidgetPicker').showModal();
  }
  $('homeRooms').addEventListener('click', function (e) { if (e.target.closest('#homeAddTile')) openPicker(); });
  var picker = $('homeWidgetPicker');
  $('homePickerClose').addEventListener('click', function () { picker.close(); });
  picker.addEventListener('click', function (e) { if (e.target === picker) picker.close(); });
  picker.addEventListener('close', function () {
    var key = justAdded[justAdded.length - 1];
    justAdded = [];
    if (!key) return;
    var el = roomsBox.querySelector('[data-key="' + CSS.escape(key) + '"]');
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.classList.add('just-added');
    setTimeout(function () { el.classList.remove('just-added'); }, 1800);
  });
  $('homeWidgetChoices').addEventListener('click', function (e) {
    var button = e.target.closest('[data-add-widget]');
    if (!button) return;
    var key = button.dataset.addWidget;
    var room = state.data.rooms.filter(function (r) { return r.key === key; })[0];
    var on = !isSelected(room);
    selectWidget(key, on);
    justAdded = justAdded.filter(function (k) { return k !== key; });
    if (on) justAdded.push(key);
    var again = $('homeWidgetChoices').querySelector('[data-add-widget="' + CSS.escape(key) + '"]');
    if (again) again.focus();
  });
  roomsBox.addEventListener('click', function (e) {
    if (!state.editing) return;
    var el = e.target.closest('.home-room[data-key]');
    if (!el) return;
    var key = el.dataset.key;
    if (e.target.closest('[data-remove]')) {
      var room = state.data.rooms.filter(function (r) { return r.key === key; })[0];
      var next = el.nextElementSibling || el.previousElementSibling;
      selectWidget(key, false);
      say('Removed ' + room.title + ' ', function () { selectWidget(key, true); });
      var focusEl = next && next.dataset.key && roomsBox.querySelector('[data-key="' + CSS.escape(next.dataset.key) + '"] [data-remove]');
      if (focusEl) focusEl.focus();
      return;
    }
    var move = e.target.closest('[data-move]');
    if (move) {
      var dir = Number(move.dataset.move);
      var sib = dir < 0 ? el.previousElementSibling : el.nextElementSibling;
      if (!sib || !sib.dataset.key) return;
      roomsBox.insertBefore(el, dir < 0 ? sib : sib.nextSibling);
      state.layout.order = currentOrder(); saveLayout(); move.focus();
      el.scrollIntoView({ block: 'nearest' });
      return;
    }
    var wb = e.target.closest('[data-width]');
    if (wb) { applySize(el, wb.dataset.width, sizeOf(el).height); saveLayout(); return; }
    if (e.target.closest('[data-height-cycle]')) {
      var cur = sizeOf(el).height, i = HEIGHTS.indexOf(cur);
      var nextH = i !== -1 ? HEIGHTS[(i + 1) % HEIGHTS.length]
        : (HEIGHTS.filter(function (h) { return h && h > cur; })[0] || null);
      applySize(el, sizeOf(el).width, nextH); saveLayout();
    }
  });
  var WIDTH_ORDER = ['s', 'm', 'l'];
  roomsBox.addEventListener('keydown', function (e) {
    var handle = e.target.closest('[data-resize]');
    if (!state.editing || !handle) return;
    var el = handle.closest('[data-key]'), size = sizeOf(el);
    var w = WIDTH_ORDER.indexOf(size.width), h = size.height;
    if (e.key === 'ArrowRight') w = Math.min(2, w + 1);
    else if (e.key === 'ArrowLeft') w = Math.max(0, w - 1);
    else if (e.key === 'ArrowDown') h = Math.min(900, (h || Math.round(el.getBoundingClientRect().height / 20) * 20) + 40);
    else if (e.key === 'ArrowUp') h = Math.max(180, (h || Math.round(el.getBoundingClientRect().height / 20) * 20) - 40);
    else if (e.key === 'Delete' || e.key === 'Backspace') h = null;
    else return;
    e.preventDefault();
    applySize(el, WIDTH_ORDER[w], h); saveLayout();
  });
  roomsBox.addEventListener('dblclick', function (e) {
    var handle = e.target.closest('[data-resize]');
    if (!state.editing || !handle) return;
    var el = handle.closest('[data-key]');
    applySize(el, sizeOf(el).width, null); saveLayout();
  });
  roomsBox.addEventListener('pointerdown', function (e) {
    var handle = e.target.closest('[data-resize]');
    if (!state.editing || !handle) return;
    e.preventDefault();
    var el = handle.closest('[data-key]'), rect = el.getBoundingClientRect();
    var x = e.clientX, y = e.clientY;
    var columns = getComputedStyle(roomsBox).gridTemplateColumns.split(' ').length;
    var gap = parseFloat(getComputedStyle(roomsBox).columnGap) || 14;
    var unit = (roomsBox.clientWidth - gap * (columns - 1)) / columns;
    handle.setPointerCapture(e.pointerId);
    el.classList.add('resizing');
    function resize(event) {
      var span = Math.max(1, Math.min(columns, Math.round((rect.width + event.clientX - x + gap) / (unit + gap))));
      var width = columns === 1 ? sizeOf(el).width : span === 1 ? 's' : span >= columns ? 'l' : 'm';
      var height = Math.max(180, Math.min(900, Math.round((rect.height + event.clientY - y) / 20) * 20));
      applySize(el, width, height);
    }
    function finish() {
      handle.removeEventListener('pointermove', resize); handle.removeEventListener('pointerup', finish); handle.removeEventListener('pointercancel', finish);
      el.classList.remove('resizing');
      saveLayout();
    }
    handle.addEventListener('pointermove', resize); handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish);
  });
  /* reorder: pointer events cover mouse, pen and touch with one code path */
  roomsBox.addEventListener('pointerdown', function (e) {
    var grip = e.target.closest('[data-drag]');
    if (!state.editing || !grip || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault();
    var el = grip.closest('.home-room[data-key]'), x = e.clientX, y = e.clientY, raf = 0;
    el.classList.add('dragging');
    function reorder() {
      var under = document.elementFromPoint(x, y);
      var over = under && under.closest('.home-room[data-key]');
      if (!over || over === el || over.parentNode !== roomsBox) return;
      var rect = over.getBoundingClientRect();
      var after = rect.height > rect.width * .6 || over.classList.contains('l')
        ? (y - rect.top) > rect.height / 2
        : (x - rect.left) > rect.width / 2;
      var ref = after ? over.nextSibling : over;
      if (ref !== el && ref !== el.nextSibling) roomsBox.insertBefore(el, ref);
    }
    function tick() {
      var edge = 70, step = y < edge ? -(edge - y) / 5 : y > innerHeight - edge ? (y - (innerHeight - edge)) / 5 : 0;
      if (step) { scrollBy(0, step); reorder(); }
      raf = requestAnimationFrame(tick);
    }
    function move(ev) { x = ev.clientX; y = ev.clientY; reorder(); }
    function finish() {
      cancelAnimationFrame(raf);
      removeEventListener('pointermove', move); removeEventListener('pointerup', finish); removeEventListener('pointercancel', finish);
      el.classList.remove('dragging');
      var order = currentOrder();
      if (order.join() !== (state.layout.order || []).join()) { state.layout.order = order; saveLayout(); }
      grip.focus();
    }
    addEventListener('pointermove', move); addEventListener('pointerup', finish); addEventListener('pointercancel', finish);
    raf = requestAnimationFrame(tick);
  });

  /* ---------------- load + live poll ---------------- */
  function applyLive(d) {
    if (!state.data) return;
    Object.assign(state.data, d);
    renderNeeds(d.needs);
    renderLive(d.live);
    renderHouse(d.household);
    renderRecent(d.recent);
    renderHeader(state.data);
  }
  async function load() {
    var d;
    try {
      d = await Tomo.api('/api/home?tz=' + (-new Date().getTimezoneOffset()));
    } catch (e) {
      $('homeSummary').textContent = 'Could not load the house: ' + ((e && e.message) || 'error');
      return;
    }
    if (!d) return;
    state.data = d;
    (d.rooms || []).forEach(function (r) {
      if (r.refresh_seconds) cardDue[r.key] = Date.now() + r.refresh_seconds * 1000;
    });
    state.layout = d.layout || { order: [], hidden: [] };
    renderCoordinator(d.coordinator);
    renderHeader(d);
    renderFoundations(d.foundations);
    renderNeeds(d.needs);
    renderLive(d.live);
    renderToday(d.today);
    renderRecent(d.recent);
    renderRooms();
    renderHouse(d.household);
    renderStarters();
  }

  var cardDue = Object.create(null), cardsPolling = false;
  async function refreshCards() {
    if (cardsPolling || !state.data || document.hidden || state.editing) return;
    var now = Date.now();
    var due = state.data.rooms.filter(function (r) {
      return r.refresh_seconds && isSelected(r) && now >= (cardDue[r.key] || 0);
    });
    if (!due.length) return;
    cardsPolling = true;
    var params = new URLSearchParams();
    due.forEach(function (r) { params.append('keys', r.key); });
    try {
      var d = await Tomo.api('/api/home/cards?' + params.toString());
      due.forEach(function (r) {
        var fresh = (d.cards || []).find(function (c) { return c.key === r.key; });
        if (!fresh || fresh.error) r.stale = fresh && fresh.error || 'Widget unavailable';
        else {
          var el = $('homeRooms').querySelector('[data-key="' + CSS.escape(r.key) + '"]');
          if (el && el.contains(document.activeElement)) return;
          Object.assign(r, fresh); delete r.error; delete r.stale;
        }
      });
    } catch (e) {
      due.forEach(function (r) { r.stale = 'Could not refresh'; });
    } finally {
      due.forEach(function (r) {
        cardDue[r.key] = Date.now() + r.refresh_seconds * 1000;
        updateRoom(r);
      });
      cardsPolling = false;
    }
  }
  setInterval(refreshCards, 1000);

  var polling = false;
  async function refreshLive() {
    if (polling || !state.data || document.hidden) return;
    polling = true;
    try {
      var d = await Tomo.api('/api/home/live');
      if (d) applyLive(d);
    } catch (e) {} finally { polling = false; }
  }
  setInterval(refreshLive, 5000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) { refreshLive(); refreshCards(); }
  });

  resizeInput();
  syncSend();
  load();
  loadPrompts();
  loadWorkplaces();
})();

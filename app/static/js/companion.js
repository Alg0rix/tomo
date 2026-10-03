/* Companion · 交換日記 — the exchange diary between you and Tomo.
 *
 * Cover (bond seal + stage), the diary Tomo keeps after each look-back,
 * and side notes: what the learning loop is doing now, what Tomo knows
 * about you (editable), your rhythm, and the skills you share.
 */
(function () {
  'use strict';

  var root = document.getElementById('companionRoot');
  if (!root) return;

  var TZ = -new Date().getTimezoneOffset();
  var WEEKDAYS = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];
  var GLYPH = { fact: '記', skill: '技', episode: '話', file: '紙', state: '状', note: '記' };
  var FACTS_FOLDED = 6;

  var state = {
    data: null,
    entries: [],
    hasMore: false,
    nextBefore: null,
    filter: 'all',
    loading: false,
    factsOpen: false,
    editing: null,
    forgetting: null,
  };

  // ── helpers ──────────────────────────────────────────────────────────
  function esc(s) {
    return Tomo.escapeHtml(s == null ? '' : String(s));
  }

  function plural(n, one, many) {
    return n + ' ' + (n === 1 ? one : many || one + 's');
  }

  function localDay(ts) {
    var d = new Date(Number(ts) * 1000);
    return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }

  function clock(ts) {
    return new Date(Number(ts) * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  function longDate(ts) {
    return new Date(Number(ts) * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function ago(ts) {
    if (!ts) return '';
    var s = Math.max(0, Date.now() / 1000 - Number(ts));
    if (s < 60) return 'just now';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    if (s < 86400 * 30) return Math.round(s / 86400) + 'd ago';
    return longDate(ts);
  }

  function dayLabel(iso) {
    var today = localDay(Date.now() / 1000);
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (iso === today) return 'Today';
    if (iso === localDay(y.getTime() / 1000)) return 'Yesterday';
    return '';
  }

  function userName() {
    var u = root.getAttribute('data-username') || '';
    return u && u !== 'web' ? u : 'You';
  }

  // ── cover ────────────────────────────────────────────────────────────
  function seal(d) {
    var parts = d.bond_parts || [];
    var R = 84, C = 2 * Math.PI * R, GAP = 7;
    var usable = C - GAP * parts.length;
    var at = 0;
    var arcs = parts.map(function (p, i) {
      var len = usable * (p.max / 100);
      var fill = Math.max(0, Math.min(1, p.ratio)) * len;
      var off = -at;
      at += len + GAP;
      return '<circle class="kn-arc-track" r="' + R + '" cx="100" cy="100" stroke-dasharray="' + len.toFixed(2) + ' ' + C.toFixed(2) +
        '" stroke-dashoffset="' + off.toFixed(2) + '"/>' +
        '<circle class="kn-arc" r="' + R + '" cx="100" cy="100" style="--len:' + fill.toFixed(2) + ';--i:' + i +
        '" stroke-dasharray="' + fill.toFixed(2) + ' ' + C.toFixed(2) + '" stroke-dashoffset="' + off.toFixed(2) + '">' +
        '<title>' + esc(p.label + ': ' + p.points + ' / ' + p.max) + '</title></circle>';
    }).join('');
    var st = d.stage || {};
    return '<div class="kn-seal" role="img" aria-label="Bond ' + d.bond + ' of 100, stage ' + esc(st.name) + '">' +
      '<svg viewBox="0 0 200 200" aria-hidden="true"><g transform="rotate(-90 100 100)">' + arcs + '</g></svg>' +
      '<div class="kn-hanko"><span class="kn-hanko-k">' + esc(st.kanji) + '</span></div>' +
      '</div>';
  }

  function partsLegend(d) {
    return '<ul class="kn-parts">' + (d.bond_parts || []).map(function (p) {
      var pct = Math.round(Math.max(0, Math.min(1, p.ratio)) * 100);
      return '<li title="' + esc(p.hint) + '"><span class="kn-part-name">' + esc(p.label) + '</span>' +
        '<span class="kn-part-bar"><i style="width:' + pct + '%"></i></span>' +
        '<span class="kn-part-pts">' + Math.round(p.points) + '<small>/' + p.max + '</small></span></li>';
    }).join('') + '</ul>';
  }

  function cover(d) {
    var st = d.stage || {};
    var s = d.stats || {};
    var r = d.rhythm || {};
    var next = st.next;
    var mark = root.getAttribute('data-mark');
    var since = d.first_seen_at ? 'since ' + longDate(d.first_seen_at) : 'starting today';
    var progress = Math.round((st.progress || 0) * 100);
    var nextLine = next
      ? '<strong>' + plural(next.to_go, 'point') + '</strong> to <span class="kn-k">' + esc(next.kanji) + '</span> ' + esc(next.name)
      : 'The deepest bond there is. <span class="kn-k">親友</span>';
    return '<header class="kn-cover">' +
      '<div class="kn-cover-main">' +
        '<div class="kn-label"><span class="kn-k">交換日記</span><span>exchange diary</span></div>' +
        '<h1 class="kn-title">' + esc(userName()) + ' <span class="kn-amp">&amp;</span> ' +
          '<span class="kn-tomo">' + (mark ? '<img src="' + esc(mark) + '" alt="">' : '') + 'Tomo</span></h1>' +
        '<p class="kn-since">Day <strong>' + ((d.days_together || 0) + 1) + '</strong> together · ' + esc(since) + '</p>' +
        '<dl class="kn-tally">' +
          '<div><dt>chats</dt><dd>' + (s.chats || 0) + '</dd></div>' +
          '<div><dt>lessons kept</dt><dd>' + (s.events_saved || 0) + '</dd></div>' +
          '<div><dt>about you</dt><dd>' + (s.profile_facts || 0) + '</dd></div>' +
          '<div><dt>day streak</dt><dd>' + (r.streak || 0) + '</dd></div>' +
        '</dl>' +
        '<div class="kn-next">' +
          '<div class="kn-next-row"><span>' + nextLine + '</span><span class="kn-next-pct">' + progress + '%</span></div>' +
          '<div class="kn-next-bar"><i style="width:' + progress + '%"></i></div>' +
          (st.grow ? '<p class="kn-grow"><span>Grow it</span>' + esc(st.grow.hint) + '</p>' : '') +
        '</div>' +
      '</div>' +
      '<div class="kn-cover-bond">' +
        seal(d) +
        '<div class="kn-stage"><span class="kn-stage-name">' + esc(st.name) + '</span>' +
          '<span class="kn-stage-romaji">' + esc(st.romaji) + ' · bond ' + d.bond + '/100</span></div>' +
        partsLegend(d) +
      '</div>' +
    '</header>';
  }

  // ── diary ────────────────────────────────────────────────────────────
  function learnedItem(x) {
    var head;
    if (x.kind === 'fact' && x.fact) {
      var where = x.entity === 'user/profile' ? 'about you' : x.entity;
      head = '<span class="kn-verb">' + esc(x.verb) + '</span> ' +
        (x.href ? '<a class="kn-chip" href="' + esc(x.href) + '">' + esc(where) + '</a>' : '<span class="kn-chip">' + esc(where) + '</span>') +
        '<q class="kn-fact">' + esc(x.fact) + '</q>';
    } else if (x.kind === 'skill' && x.name) {
      head = '<span class="kn-verb">' + esc(x.verb) + ' skill</span> ' +
        (x.href ? '<a class="kn-chip" href="' + esc(x.href) + '">' + esc(x.name) + '</a>' : '<span class="kn-chip">' + esc(x.name) + '</span>');
    } else if (x.kind === 'episode' && x.title) {
      head = '<span class="kn-verb">remembered</span> <span class="kn-plain">' + esc(x.title) + '</span>';
    } else {
      head = '<span class="kn-plain">' + esc(x.text) + '</span>';
    }
    return '<li class="kn-li k-' + esc(x.kind) + '"><span class="kn-glyph" aria-hidden="true">' + (GLYPH[x.kind] || '記') + '</span><div>' + head + '</div></li>';
  }

  function chatLink(sess, prefix) {
    if (!sess) return '';
    var title = sess.title || 'a chat';
    if (!sess.exists) return '<span class="kn-from">' + esc(prefix) + ' a chat that was deleted</span>';
    return '<a class="kn-from" href="/sessions?s=' + encodeURIComponent(sess.id) + '">' + esc(prefix) + ' “' + esc(title) + '” ↗</a>';
  }

  function learnedEntry(e) {
    // A story synthesized from the items just repeats them; only show a real diary line.
    var learned = e.learned || [];
    var echo = e.story && learned.length && learned.every(function (x) { return x.text && e.story.indexOf(x.text) !== -1; });
    var story = e.story && !echo ? '<p class="kn-story">' + esc(e.story) + '</p>' : '';
    return '<article class="kn-entry is-learned">' +
      '<time class="kn-time">' + esc(clock(e.created_at)) + '</time>' +
      '<div class="kn-page">' + story +
        '<ul class="kn-learned">' + learned.map(learnedItem).join('') + '</ul>' +
        '<footer>' + chatLink(e.session, 'from') + '</footer>' +
      '</div></article>';
  }

  function quietRun(run) {
    var skipped = run.filter(function (e) { return e.status === 'skipped'; }).length;
    var quiet = run.length - skipped;
    var bits = [];
    if (quiet === 1 && !skipped) {
      var e = run[0];
      var t = e.session && e.session.exists && e.session.title
        ? 'Looked back at ' + chatLink(e.session, '').replace('↗', '').trim()
        : 'Looked back at a chat';
      bits.push(t + ' — nothing new to keep.');
    } else if (quiet) {
      bits.push('Looked back ' + plural(quiet, 'time') + ' — nothing new to keep.');
    }
    if (skipped) {
      bits.push(skipped === 1
        ? 'One look-back didn’t finish — the model returned nothing.'
        : plural(skipped, 'look-back') + ' didn’t finish — the model returned nothing.');
    }
    return '<div class="kn-entry is-quiet' + (skipped && !quiet ? ' is-skipped' : '') + '">' +
      '<time class="kn-time">' + esc(clock(run[0].created_at)) + '</time>' +
      '<p>' + bits.join(' ') + '</p></div>';
  }

  function dayGroups(entries) {
    var days = [];
    entries.forEach(function (e) {
      var key = localDay(e.created_at);
      var last = days[days.length - 1];
      if (!last || last.key !== key) days.push(last = { key: key, ts: e.created_at, items: [] });
      last.items.push(e);
    });
    return days;
  }

  function renderDay(day) {
    var body = [];
    var run = [];
    function flush() { if (run.length) { body.push(quietRun(run)); run = []; } }
    day.items.forEach(function (e) {
      if (e.status === 'learned') { flush(); body.push(learnedEntry(e)); }
      else run.push(e);
    });
    flush();
    var dt = new Date(Number(day.ts) * 1000);
    var rel = dayLabel(day.key);
    return '<section class="kn-day">' +
      '<header class="kn-day-head">' +
        '<span class="kn-day-num">' + dt.getDate() + '</span>' +
        '<span class="kn-day-meta"><b>' + esc(dt.toLocaleDateString(undefined, { month: 'long' })) + '</b>' +
        esc(rel || dt.toLocaleDateString(undefined, { weekday: 'long' })) + '</span>' +
      '</header>' +
      '<div class="kn-day-body">' + body.join('') + '</div></section>';
  }

  function diaryBody() {
    if (!state.entries.length) {
      var learning = state.data && state.data.learning;
      var every = learning && learning.memory_nudge ? 'every ' + plural(learning.memory_nudge, 'chat') : 'after a few chats';
      return '<div class="kn-blank"><span class="kn-k">白</span>' +
        (state.filter === 'learned'
          ? '<p>No lessons kept yet.</p><span>Tomo writes here when a look-back finds something worth remembering.</span>'
          : '<p>The first page is still blank.</p><span>Tomo looks back ' + esc(every) + ' and writes down what it learned about you and your work.</span>') +
        '</div>';
    }
    return dayGroups(state.entries).map(renderDay).join('') +
      (state.hasMore
        ? '<div class="kn-more"><button type="button" class="kn-btn" data-act="more">' + (state.loading ? 'Turning…' : 'Turn to older pages') + '</button></div>'
        : '<p class="kn-end">— first page —</p>');
  }

  function diary() {
    return '<section class="kn-diary" aria-labelledby="knDiaryH">' +
      '<div class="kn-sec-head">' +
        '<h2 id="knDiaryH"><span class="kn-k">日記</span> Diary</h2>' +
        '<div class="kn-seg" role="group" aria-label="Diary filter">' +
          '<button type="button" data-act="filter" data-filter="all" aria-pressed="' + (state.filter === 'all') + '">Everything</button>' +
          '<button type="button" data-act="filter" data-filter="learned" aria-pressed="' + (state.filter === 'learned') + '">Lessons only</button>' +
        '</div>' +
      '</div>' +
      '<div id="knDiary">' + diaryBody() + '</div></section>';
  }

  // ── side notes ───────────────────────────────────────────────────────
  function nowCard(d) {
    var L = d.learning || {};
    var s = d.stats || {};
    var line;
    switch (L.mode) {
      case 'off': line = 'Learning is paused. Tomo chats as usual but keeps nothing new.'; break;
      case 'reviewing': line = 'Looking back at the last chat right now…'; break;
      case 'resting': line = 'Resting after a look-back — ready again in ' + Math.max(1, Math.round(L.cooldown_remaining_sec)) + 's.'; break;
      case 'due': line = 'A look-back is due after the next reply.'; break;
      default:
        line = L.turns_until_review
          ? 'Listening. Next look-back after ' + plural(L.turns_until_review, 'more chat') + '.'
          : 'Listening.';
    }
    var dots = '';
    if (L.memory_nudge && L.mode !== 'off') {
      var n = Math.min(L.memory_nudge, 12);
      var filled = Math.min(n, Math.round((L.turns_since_review / L.memory_nudge) * n));
      for (var i = 0; i < n; i++) dots += '<i' + (i < filled ? ' class="on"' : '') + '></i>';
      dots = '<div class="kn-dots" aria-hidden="true">' + dots + '</div>';
    }
    var tally = (s.events_total || 0)
      ? plural(s.events_total, 'look-back') + ' so far · ' + (s.events_saved || 0) + ' kept something'
      : 'No look-backs yet';
    if (L.last_review_at) tally += ' · last ' + ago(L.last_review_at);
    return '<section class="kn-card kn-now" data-mode="' + esc(L.mode || 'listening') + '">' +
      '<div class="kn-card-head"><h3><span class="kn-k">今</span> Tomo now</h3>' +
        '<label class="toggle" title="Learning loop"><input type="checkbox" data-act="learning"' + (d.learning_enabled ? ' checked' : '') +
        ' aria-label="Learning loop"><span class="track"></span></label></div>' +
      '<p class="kn-now-line"><span class="kn-pulse" aria-hidden="true"></span>' + esc(line) + '</p>' + dots +
      '<p class="kn-faint">' + esc(tally) + '</p></section>';
  }

  function factRow(f) {
    var n = f.number;
    if (state.editing === n) {
      return '<li class="kn-fact-row is-editing" data-n="' + n + '">' +
        '<textarea class="kn-input" rows="3" data-role="edit">' + esc(f.text) + '</textarea>' +
        '<div class="kn-row-acts"><button type="button" class="kn-btn sm" data-act="cancel">Cancel</button>' +
        '<button type="button" class="kn-btn sm primary" data-act="save" data-n="' + n + '">Save</button></div></li>';
    }
    if (state.forgetting === n) {
      return '<li class="kn-fact-row is-forgetting" data-n="' + n + '">' +
        '<span class="kn-fact-text">' + esc(f.text) + '</span>' +
        '<div class="kn-row-acts"><span>Forget this?</span><button type="button" class="kn-btn sm" data-act="cancel">Keep</button>' +
        '<button type="button" class="kn-btn sm danger" data-act="forget-yes" data-n="' + n + '">Forget</button></div></li>';
    }
    return '<li class="kn-fact-row" data-n="' + n + '">' +
      '<span class="kn-fact-text">' + esc(f.text) + (f.origin === 'user' ? ' <span class="kn-yours" title="You wrote this">you</span>' : '') + '</span>' +
      '<span class="kn-fact-tools">' +
        '<button type="button" class="kn-icon" data-act="edit" data-n="' + n + '" title="Correct" aria-label="Correct this">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20h4L19 9l-4-4L4 16v4z"/></svg></button>' +
        '<button type="button" class="kn-icon" data-act="forget" data-n="' + n + '" title="Forget" aria-label="Forget this">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 6l12 12M18 6 6 18"/></svg></button>' +
      '</span></li>';
  }

  function aboutCard(d) {
    var p = d.profile || { facts: [], total: 0 };
    var facts = p.facts || [];
    var shown = state.factsOpen ? facts : facts.slice(0, FACTS_FOLDED);
    var list = facts.length
      ? '<ol class="kn-facts">' + shown.map(factRow).join('') + '</ol>' +
        (facts.length > FACTS_FOLDED
          ? '<button type="button" class="kn-link" data-act="facts">' + (state.factsOpen ? 'Show fewer' : 'Show all ' + facts.length) + '</button>'
          : '')
      : '<p class="kn-faint kn-pad">Nothing yet. Tell Tomo how you like to work — it remembers across every chat.</p>';
    var pages = d.vault_pages || {};
    var also = Object.keys(pages).map(function (k) { return plural(pages[k], k); });
    if ((d.stats || {}).episodes) also.push(plural(d.stats.episodes, 'episode'));
    return '<section class="kn-card kn-about" id="knAbout">' +
      '<div class="kn-card-head"><h3><span class="kn-k">覚</span> What Tomo knows about you</h3>' +
        '<span class="kn-count">' + (p.total || 0) + '</span></div>' +
      list +
      '<form class="kn-teach" data-act="teach"><input class="kn-input" name="fact" maxlength="500" autocomplete="off" placeholder="Teach Tomo something about you…" aria-label="Teach Tomo something about you">' +
        '<button type="submit" class="kn-btn sm">Add</button></form>' +
      '<p class="kn-faint kn-also">' + (also.length ? 'Also remembers ' + esc(also.join(' · ')) + ' · ' : '') +
        '<a href="/memory">Open memory →</a></p></section>';
  }

  function rhythmCard(d) {
    var r = d.rhythm || {};
    var days = r.days || [];
    var max = r.max_intensity || 0;
    var months = [];
    var cells = days.map(function (x, i) {
      var lv = !x.intensity ? 0 : max <= 1 ? 2 : Math.min(4, Math.ceil((x.intensity / max) * 4));
      if (x.date.slice(8) <= '07' && x.weekday === 0) months.push({ col: Math.floor(i / 7), m: x.date });
      var tip = x.date + ' · ' + plural(x.chats, 'chat') + (x.saves ? ' · ' + plural(x.saves, 'lesson') + ' kept' : '');
      return '<i class="lv' + lv + (x.saves ? ' kept' : '') + (x.date === r.today ? ' today' : '') + '" title="' + esc(tip) + '"></i>';
    }).join('');
    var monthRow = months.map(function (m) {
      var name = new Date(m.m + 'T12:00:00').toLocaleDateString(undefined, { month: 'short' });
      return '<span style="grid-column:' + (m.col + 1) + '">' + esc(name) + '</span>';
    }).join('');
    var bits = [plural(r.streak || 0, 'day') + ' streak'];
    if (r.longest_streak) bits.push('longest ' + plural(r.longest_streak, 'day'));
    if (r.favourite_weekday != null) bits.push(WEEKDAYS[r.favourite_weekday] + ' are your day');
    return '<section class="kn-card kn-rhythm">' +
      '<div class="kn-card-head"><h3><span class="kn-k">季</span> Rhythm</h3><span class="kn-faint">' + (r.weeks || 20) + ' weeks</span></div>' +
      '<div class="kn-heat" style="--weeks:' + Math.ceil(days.length / 7) + '">' +
        '<div class="kn-heat-months">' + monthRow + '</div>' +
        '<div class="kn-heat-grid" role="img" aria-label="Activity over ' + (r.weeks || 20) + ' weeks">' + cells + '</div>' +
      '</div>' +
      '<p class="kn-faint">' + esc(bits.join(' · ')) + '</p>' +
      '<p class="kn-legend"><i class="lv1"></i><i class="lv2"></i><i class="lv4"></i> chats <i class="lv2 kept"></i> lesson kept</p>' +
      '</section>';
  }

  function skillsCard(d) {
    var sk = d.skills || {};
    var rows = sk.most_used || [];
    var top = rows.length ? rows[0].use_count : 1;
    var body = rows.length
      ? '<ol class="kn-skills">' + rows.map(function (s) {
          return '<li><a href="/skills/' + encodeURIComponent(s.id) + '"><span>' + esc(s.name) + '</span>' +
            '<span class="kn-skill-bar"><i style="width:' + Math.round((s.use_count / top) * 100) + '%"></i></span>' +
            '<span class="kn-faint">' + s.use_count + '×</span></a></li>';
        }).join('') + '</ol>'
      : '<p class="kn-faint kn-pad">No shared skills yet. When a workflow repeats, Tomo can turn it into one.</p>';
    return '<section class="kn-card kn-skillcard">' +
      '<div class="kn-card-head"><h3><span class="kn-k">技</span> Skills you share</h3>' +
        '<span class="kn-count">' + (sk.count || 0) + '</span></div>' + body +
      (sk.library ? '<p class="kn-faint kn-also">' + plural(sk.library, 'skill') + ' in your library · <a href="/skills">All skills →</a></p>' : '') +
      '</section>';
  }

  // ── render ───────────────────────────────────────────────────────────
  function render() {
    var d = state.data;
    root.innerHTML = cover(d) +
      '<div class="kn-grid">' +
        '<div class="kn-main">' + diary() + '</div>' +
        '<aside class="kn-side">' + nowCard(d) + aboutCard(d) + rhythmCard(d) + skillsCard(d) + '</aside>' +
      '</div>';
  }

  function rerender(id, html) {
    var el = document.getElementById(id);
    if (el) el.outerHTML = html;
  }

  function redrawDiary() {
    var el = document.getElementById('knDiary');
    if (el) el.innerHTML = diaryBody();
    root.querySelectorAll('[data-act="filter"]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-filter') === state.filter));
    });
  }

  function setPage(page, append) {
    state.entries = append ? state.entries.concat(page.entries || []) : (page.entries || []);
    state.hasMore = !!page.has_more;
    state.nextBefore = page.next_before;
  }

  async function fetchDiary(append) {
    if (state.loading) return;
    state.loading = true;
    if (append) redrawDiary();
    try {
      var q = '/api/companion/events?limit=30' + (state.filter === 'learned' ? '&saved_only=true' : '');
      if (append && state.nextBefore) q += '&before=' + encodeURIComponent(state.nextBefore);
      var page = await Tomo.api(q);
      setPage(page || {}, append);
    } catch (e) {
      Tomo.toast('Could not turn the page', 'error');
    } finally {
      state.loading = false;
      redrawDiary();
    }
  }

  async function refreshAbout() {
    var d = await Tomo.api('/api/companion?tz=' + TZ);
    if (!d) return;
    var keep = state.entries;
    state.data = d;
    state.entries = keep;
    rerender('knAbout', aboutCard(d));
  }

  function memoryPost(path, body) {
    return Tomo.api('/api/memory/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  function factByNumber(n) {
    return ((state.data.profile || {}).facts || []).filter(function (f) { return f.number === n; })[0];
  }

  async function act(kind, el) {
    var n = el.hasAttribute('data-n') ? Number(el.getAttribute('data-n')) : null;
    switch (kind) {
      case 'more': return fetchDiary(true);
      case 'filter': {
        var f = el.getAttribute('data-filter');
        if (f === state.filter) return;
        state.filter = f;
        state.entries = [];
        state.hasMore = false;
        redrawDiary();
        return fetchDiary(false);
      }
      case 'facts': state.factsOpen = !state.factsOpen; break;
      case 'edit': state.editing = n; state.forgetting = null; break;
      case 'forget': state.forgetting = n; state.editing = null; break;
      case 'cancel': state.editing = state.forgetting = null; break;
      case 'save': {
        var ta = root.querySelector('[data-role="edit"]');
        var text = ta ? ta.value.trim() : '';
        var fact = factByNumber(n);
        if (!text || !fact) return;
        try {
          await memoryPost('entity/user/profile/edit', { number: n, text: text, expected: fact.text });
          state.editing = null;
          Tomo.toast('Corrected. Tomo will use the new version.');
          return refreshAbout();
        } catch (e) { Tomo.toast(e.message || 'Could not save', 'error'); return; }
      }
      case 'forget-yes': {
        try {
          await memoryPost('entity/user/profile/forget', { number: n });
          state.forgetting = null;
          Tomo.toast('Forgotten.');
          return refreshAbout();
        } catch (e) { Tomo.toast(e.message || 'Could not forget', 'error'); return; }
      }
      default: return;
    }
    rerender('knAbout', aboutCard(state.data));
    var focusEl = root.querySelector('[data-role="edit"]');
    if (focusEl) { focusEl.focus(); focusEl.setSelectionRange(focusEl.value.length, focusEl.value.length); }
  }

  root.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-act]');
    if (!el || el.tagName === 'FORM' || el.tagName === 'INPUT') return;
    act(el.getAttribute('data-act'), el);
  });

  root.addEventListener('keydown', function (ev) {
    if (ev.target.getAttribute('data-role') !== 'edit') return;
    if (ev.key === 'Escape') act('cancel', ev.target);
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {
      var save = root.querySelector('[data-act="save"]');
      if (save) act('save', save);
    }
  });

  root.addEventListener('submit', async function (ev) {
    var form = ev.target.closest('[data-act="teach"]');
    if (!form) return;
    ev.preventDefault();
    var input = form.querySelector('input');
    var text = input.value.trim();
    if (!text) return;
    input.disabled = true;
    try {
      var res = await memoryPost('facts', { entity: 'user/profile', content: text });
      Tomo.toast(res && res.added === false ? 'Tomo already knew that.' : 'Noted. Tomo will remember.');
      await refreshAbout();
      var again = root.querySelector('.kn-teach input');
      if (again) again.focus();
    } catch (e) {
      input.disabled = false;
      Tomo.toast(e.message || 'Could not save', 'error');
    }
  });

  root.addEventListener('change', async function (ev) {
    if (ev.target.getAttribute('data-act') !== 'learning') return;
    var on = ev.target.checked;
    try {
      await Tomo.api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ learning_enabled: on }),
      });
      var d = await Tomo.api('/api/companion?tz=' + TZ);
      if (d) {
        state.data = d;
        var card = root.querySelector('.kn-now');
        if (card) card.outerHTML = nowCard(d);
      }
      Tomo.toast(on ? 'Learning on — Tomo will keep lessons again.' : 'Learning paused.');
    } catch (e) {
      ev.target.checked = !on;
      Tomo.toast('Could not update learning', 'error');
    }
  });

  async function boot() {
    try {
      var d = await Tomo.api('/api/companion?tz=' + TZ);
      if (!d) return;
      state.data = d;
      setPage(d.diary || {}, false);
      render();
      requestAnimationFrame(function () { root.classList.add('is-ready'); });
    } catch (e) {
      console.error('companion load failed', e);
      root.innerHTML = '<div class="kn-blank"><span class="kn-k">閉</span><p>The diary wouldn’t open.</p>' +
        '<span>' + esc(e && e.message ? e.message : 'Unknown error') + '</span>' +
        '<button type="button" class="kn-btn" onclick="location.reload()">Try again</button></div>';
    }
  }

  boot();
})();

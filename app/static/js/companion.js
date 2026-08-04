/* Companion — bento UI (Tomo Darkroom tokens) */
(function () {
  'use strict';

  var root = document.getElementById('companionRoot');
  if (!root) return;

  var state = {
    nextBefore: null,
    loadingMore: false,
    data: null,
    tab: 'bond',
    selectedEventId: null,
  };

  var ICO = {
    sparkles:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z"/></svg>',
    more:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>',
    calendar:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
    activity:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 12h4l2.5-6 4 12L16 9h5"/></svg>',
    check:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
    chat:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v7A2.5 2.5 0 0 1 16.5 16H10l-4 3v-3H7.5A2.5 2.5 0 0 1 5 13.5v-7z"/></svg>',
    flame:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3c2 3 1 5 1 5s3-1 4 2c1 2 0 6-5 8-5-2-6-6-5-8 1-3 4-2 4-2s-1-2 1-5z"/></svg>',
  };

  function esc(s) {
    return Tomo.escapeHtml(s == null ? '' : s);
  }

  function fmtDate(ts) {
    if (!ts) return '';
    try {
      return new Date(Number(ts) * 1000).toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch (e) {
      return '';
    }
  }

  function fmtDay(iso) {
    if (!iso) return '';
    try {
      return new Date(iso + 'T12:00:00Z').toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
    } catch (e) {
      return iso;
    }
  }

  function monthShort(ym) {
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var mi = parseInt(String(ym).slice(5, 7), 10) - 1;
    return months[mi] || ym;
  }

  function heatLevel(intensity, maxI) {
    if (!intensity) return 0;
    if (!maxI || maxI <= 1) return intensity > 0 ? 2 : 0;
    var r = intensity / maxI;
    if (r > 0.75) return 4;
    if (r > 0.5) return 3;
    if (r > 0.25) return 2;
    return 1;
  }

  function displayName(data) {
    var el = document.getElementById('companionRoot');
    var user = (el && el.getAttribute('data-username')) || '';
    if (user && user !== 'web') return user + "'s Tomo";
    return 'Your Tomo';
  }

  function renderHeatmap(hm) {
    hm = hm || {};
    var days = hm.days || [];
    var maxI = hm.max_intensity || 0;
    var cells = days
      .map(function (d) {
        var lv = heatLevel(d.intensity, maxI);
        var tip =
          fmtDay(d.date) +
          ' · ' +
          d.chats +
          ' chats, ' +
          d.saves +
          ' saves, ' +
          d.reviews +
          ' reviews';
        return (
          '<div class="cp-heat-cell lv-' +
          lv +
          '" title="' +
          esc(tip) +
          '" data-date="' +
          esc(d.date) +
          '"></div>'
        );
      })
      .join('');

    // Month labels: approximate column index → px (11 cell + 3 gap)
    var monthsHtml = (hm.months || [])
      .map(function (m) {
        var col = Math.floor((m.index || 0) / 7);
        var left = col * 14;
        return (
          '<span class="cp-heat-month" style="left:' +
          left +
          'px">' +
          esc(monthShort(m.month)) +
          '</span>'
        );
      })
      .join('');

    return (
      '<div class="cp-heatmap-wrap">' +
      '<div class="cp-heat-months" style="width:' +
      Math.max(200, Math.ceil(days.length / 7) * 14) +
      'px">' +
      monthsHtml +
      '</div>' +
      '<div class="cp-heatmap" role="img" aria-label="Activity heatmap">' +
      cells +
      '</div></div>' +
      '<div class="cp-heat-legend">' +
      '<span>Less</span>' +
      '<div class="cp-heat-cell lv-0"></div>' +
      '<div class="cp-heat-cell lv-1"></div>' +
      '<div class="cp-heat-cell lv-2"></div>' +
      '<div class="cp-heat-cell lv-3"></div>' +
      '<div class="cp-heat-cell lv-4"></div>' +
      '<span>More</span></div>'
    );
  }

  function renderGrowthBars(growth) {
    growth = growth || [];
    var max = 1;
    growth.forEach(function (g) {
      if ((g.events || 0) > max) max = g.events;
    });
    var bars = growth
      .map(function (g) {
        var h = Math.max(6, Math.round(((g.events || 0) / max) * 80));
        var sh = Math.max(0, Math.round(((g.saved || 0) / max) * 80));
        return (
          '<div class="companion-bar-col" title="' +
          esc(g.month) +
          ': ' +
          (g.events || 0) +
          ' reviews, ' +
          (g.saved || 0) +
          ' saved">' +
          '<div class="companion-bar-track">' +
          '<div class="companion-bar" style="height:' +
          h +
          'px">' +
          (sh
            ? '<div class="companion-bar-saved" style="height:' + sh + 'px"></div>'
            : '') +
          '</div></div>' +
          '<div class="companion-bar-label mono">' +
          esc(monthShort(g.month)) +
          '</div></div>'
        );
      })
      .join('');
    return (
      '<div class="companion-growth-chart">' +
      bars +
      '</div>' +
      '<p class="faint companion-chart-legend" style="margin-top:12px;font-size:12px;color:var(--text-faint)">Bars = learning reviews · filled = saved lessons (12 months)</p>'
    );
  }

  // Keep growth chart styles (from earlier) scoped here if missing in css
  function ensureGrowthStyles() {
    if (document.getElementById('cp-growth-inline')) return;
    var s = document.createElement('style');
    s.id = 'cp-growth-inline';
    s.textContent =
      '.companion-growth-chart{display:flex;align-items:flex-end;gap:6px;min-height:100px}' +
      '.companion-bar-col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}' +
      '.companion-bar-track{height:84px;width:100%;display:flex;align-items:flex-end;justify-content:center}' +
      '.companion-bar{width:70%;max-width:28px;min-height:4px;border-radius:4px 4px 2px 2px;background:var(--surface-3);border:1px solid var(--border);position:relative;overflow:hidden;display:flex;align-items:flex-end}' +
      '.companion-bar-saved{width:100%;background:var(--accent);border-radius:2px 2px 0 0;min-height:2px;box-shadow:0 0 8px var(--accent-glow)}' +
      '.companion-bar-label{font-size:9px;color:var(--text-faint);font-family:var(--font-mono)}';
    document.head.appendChild(s);
  }

  function diaryFeature(ev) {
    if (!ev) return '';
    if (ev.saved) {
      return esc(ev.diary || ev.note || 'A durable lesson was recorded for future sessions.');
    }
    return esc(ev.note || 'Nothing durable to save this pass.');
  }

  function timelineBullets(ev) {
    var items = [];
    if (ev.saved) items.push('Lesson saved');
    else items.push('Review completed (idle)');
    if (ev.review_memory) items.push('Memory focus');
    if (ev.review_skills) items.push('Skills focus');
    (ev.actions || []).slice(0, 3).forEach(function (a) {
      items.push(String(a).splitlines()[0].slice(0, 80));
    });
    if (!items.length) return '';
    return (
      '<ul class="cp-tl-bullets">' +
      items
        .map(function (t) {
          return '<li>' + ICO.check + '<span>' + esc(t) + '</span></li>';
        })
        .join('') +
      '</ul>'
    );
  }

  function renderTimeline(events) {
    if (!events || !events.length) {
      return (
        '<div class="cp-empty">No milestones yet. Keep Learning on and work multi-step tasks — Tomo journals here.</div>'
      );
    }
    var sel = state.selectedEventId || events[0].id;
    return events
      .map(function (ev, i) {
        var active = String(ev.id) === String(sel);
        var saved = !!ev.saved;
        var text = saved
          ? ev.diary || 'Saved a lesson'
          : ev.note || 'Nothing to save';
        return (
          '<div class="cp-tl-item' +
          (saved ? ' is-saved' : '') +
          (active ? ' is-active' : '') +
          '" data-event-id="' +
          esc(ev.id) +
          '" role="button" tabindex="0">' +
          '<span class="cp-tl-dot"></span>' +
          '<div class="cp-tl-date">' +
          esc(fmtDate(ev.created_at)) +
          (ev.agent_id ? ' · ' + esc(ev.agent_id) : '') +
          '</div>' +
          '<div class="cp-tl-text">' +
          esc(String(text).slice(0, 160)) +
          '</div>' +
          (active ? timelineBullets(ev) : '') +
          '</div>'
        );
      })
      .join('');
  }

  function renderDiaryCard(events) {
    if (!events || !events.length) {
      return (
        '<div class="cp-diary-card"><div class="cp-diary-meta">' +
        '<div class="cp-diary-title">' +
        ICO.sparkles +
        ' Learning diary</div></div>' +
        '<div class="cp-diary-body is-idle">When Tomo distills preferences or playbooks after a turn, the entry appears here — dated, inspectable, and durable.</div></div>'
      );
    }
    var sel =
      events.find(function (e) {
        return String(e.id) === String(state.selectedEventId);
      }) || events[0];
    var chips = '';
    if (sel.review_memory) chips += '<span class="cp-chip">memory</span>';
    if (sel.review_skills) chips += '<span class="cp-chip">skills</span>';
    chips += sel.saved
      ? '<span class="cp-chip is-ok">saved</span>'
      : '<span class="cp-chip">idle</span>';
    (sel.actions || []).slice(0, 4).forEach(function (a) {
      chips += '<span class="cp-chip mono">' + esc(String(a).slice(0, 60)) + '</span>';
    });
    return (
      '<div class="cp-diary-card">' +
      '<div class="cp-diary-meta">' +
      '<div class="cp-diary-title">' +
      ICO.sparkles +
      ' Learning diary</div>' +
      '<time class="cp-diary-date">' +
      esc(fmtDate(sel.created_at)) +
      '</time></div>' +
      '<div class="cp-diary-body' +
      (sel.saved ? '' : ' is-idle') +
      '">' +
      diaryFeature(sel) +
      '</div>' +
      '<div class="cp-diary-actions">' +
      chips +
      '</div></div>'
    );
  }

  function render(data) {
    ensureGrowthStyles();
    state.data = data;
    var bond = data.bond != null ? data.bond : 0;
    var stats = data.stats || {};
    var parts = data.bond_parts || {};
    var learning = !!data.learning_enabled;
    var events = data.recent_events || [];
    var preview = data.user_profile_preview || [];
    var streak = data.streak != null ? data.streak : (data.heatmap && data.heatmap.streak) || 0;

    if (events.length) {
      state.nextBefore = events[events.length - 1].created_at || null;
      if (!state.selectedEventId) state.selectedEventId = events[0].id;
    } else {
      state.nextBefore = null;
      state.selectedEventId = null;
    }

    var profileHtml = preview.length
      ? '<ul class="cp-profile-list">' +
        preview
          .map(function (e) {
            return '<li>' + esc(e) + '</li>';
          })
          .join('') +
        '</ul>'
      : '<div class="cp-empty">No profile notes yet. Preferences and corrections land here after learning saves.</div>';

    var name = displayName(data);
    var initial = (name.replace(/^Your\s+/i, '').charAt(0) || '友').toUpperCase();

    root.innerHTML =
      /* Header */
      '<section class="cp-bento cp-bento-pad">' +
      '<div class="cp-header">' +
      '<div class="cp-header-main">' +
      '<div class="cp-avatar-wrap">' +
      '<div class="cp-avatar" aria-hidden="true">' +
      esc(initial) +
      '</div>' +
      '<span class="cp-status' +
      (learning ? '' : ' is-off') +
      '" title="' +
      (learning ? 'Learning on' : 'Learning off') +
      '"></span></div>' +
      '<div class="cp-title-block">' +
      '<div class="cp-kicker">Companion</div>' +
      '<h1 class="cp-title">' +
      esc(name) +
      '</h1>' +
      '<p class="cp-subtitle">How Tomo grows with you — bond, lessons, and what it remembers.</p>' +
      '</div></div>' +
      '<div class="cp-header-actions">' +
      '<a class="cp-icon-btn" href="/system" title="Settings" aria-label="Settings">' +
      ICO.more +
      '</a></div></div>' +
      '<div class="cp-ribbon">' +
      '<span class="cp-pill">' +
      ICO.calendar +
      '<strong>' +
      esc(data.days_together || 0) +
      '</strong> days together</span>' +
      '<span class="cp-pill">' +
      ICO.chat +
      '<strong>' +
      esc(parts.chats || 0) +
      '</strong> chats</span>' +
      '<span class="cp-pill">' +
      ICO.flame +
      '<strong>' +
      esc(streak) +
      '</strong> day streak</span>' +
      '<span class="cp-pill"><span class="cp-dot"></span><strong>' +
      esc(stats.events_saved || 0) +
      '</strong> lessons saved</span>' +
      '<span class="cp-pill">' +
      ICO.activity +
      '<strong>' +
      esc(bond) +
      '</strong> bond</span>' +
      '</div>' +
      '<div class="cp-learn-row">' +
      '<div class="cp-learn-label"><span>Learning loop</span>' +
      '<span class="badge ' +
      (learning ? 'ok' : '') +
      '" id="learnBadge">' +
      (learning ? 'on' : 'off') +
      '</span></div>' +
      '<label class="toggle companion-toggle">' +
      '<input type="checkbox" id="companionLearning" ' +
      (learning ? 'checked' : '') +
      '><span class="track"></span></label></div></section>' +
      /* Activity */
      '<section class="cp-bento cp-bento-pad">' +
      '<div class="cp-activity-head">' +
      '<div class="cp-activity-titles">' +
      '<h3>Activity</h3>' +
      '<p id="cpTabDesc">Bond reflects real collaboration: chats, saved lessons, profile notes, and skills.</p>' +
      '</div>' +
      '<div class="cp-tabs" role="tablist" aria-label="Activity view">' +
      '<span class="cp-tab-ink" id="cpTabInk"></span>' +
      '<button type="button" class="cp-tab is-active" role="tab" data-tab="bond" aria-selected="true">Bond</button>' +
      '<button type="button" class="cp-tab" role="tab" data-tab="growth" aria-selected="false">Growth</button>' +
      '</div></div>' +
      '<div class="cp-panel" id="cpPanelBond" data-panel="bond">' +
      '<div class="cp-bond-panel">' +
      '<div class="cp-bond-score">' +
      '<div class="cp-bond-num">' +
      esc(bond) +
      '</div>' +
      '<div class="cp-bond-label">Bond</div></div>' +
      '<div>' +
      '<div class="cp-bond-meter" role="meter" aria-valuenow="' +
      bond +
      '" aria-valuemin="0" aria-valuemax="100">' +
      '<div class="cp-bond-fill" style="width:' +
      bond +
      '%"></div></div>' +
      '<div class="cp-bond-parts">' +
      '<span class="cp-bond-part">chats <span>' +
      esc(parts.chats || 0) +
      '</span></span>' +
      '<span class="cp-bond-part">saves <span>' +
      esc(parts.saved_events || 0) +
      '</span></span>' +
      '<span class="cp-bond-part">profile <span>' +
      esc(parts.user_memory_chars || 0) +
      'c</span></span>' +
      '<span class="cp-bond-part">skills <span>' +
      esc(parts.library_skills || 0) +
      '</span></span>' +
      '<span class="cp-bond-part">active days <span>' +
      esc(parts.days_active || 0) +
      '</span></span></div></div></div>' +
      renderHeatmap(data.heatmap) +
      '</div>' +
      '<div class="cp-panel" id="cpPanelGrowth" data-panel="growth" hidden>' +
      renderGrowthBars(data.growth) +
      '</div></section>' +
      /* Growth log */
      '<section class="cp-bento cp-bento-pad">' +
      '<div class="cp-log-head">' +
      '<div><h3>Growth log</h3>' +
      '<p>A record of every milestone as Tomo learns with you</p></div></div>' +
      '<div class="cp-log-grid">' +
      '<div id="cpDiary">' +
      renderDiaryCard(events) +
      '</div>' +
      '<div class="cp-timeline" id="cpTimeline">' +
      renderTimeline(events) +
      '</div></div>' +
      '<div class="cp-log-foot" id="companionLogFoot"' +
      (state.nextBefore && events.length >= 20 ? '' : ' hidden') +
      '>' +
      '<button type="button" class="btn ghost sm" id="companionLoadMore">Load more</button></div></section>' +
      /* What I know */
      '<section class="cp-bento cp-bento-pad">' +
      '<div class="cp-know-head">' +
      '<h3>What I know</h3>' +
      '<div class="cp-know-links">' +
      '<a class="btn ghost sm" href="/skills">Skills</a>' +
      '<a class="btn ghost sm" href="/system#memory">Memory</a></div></div>' +
      profileHtml +
      '</section>';

    bindControls();
    positionTabInk();
  }

  function positionTabInk() {
    var ink = document.getElementById('cpTabInk');
    var active = root.querySelector('.cp-tab.is-active');
    if (!ink || !active) return;
    ink.style.width = active.offsetWidth + 'px';
    ink.style.transform = 'translateX(' + active.offsetLeft + 'px)';
  }

  function setTab(tab) {
    state.tab = tab;
    root.querySelectorAll('.cp-tab').forEach(function (btn) {
      var on = btn.getAttribute('data-tab') === tab;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    root.querySelectorAll('.cp-panel').forEach(function (p) {
      p.hidden = p.getAttribute('data-panel') !== tab;
    });
    var desc = document.getElementById('cpTabDesc');
    if (desc) {
      desc.textContent =
        tab === 'growth'
          ? 'Monthly learning reviews and saved lessons — the curve of how Tomo improves its playbooks.'
          : 'Bond reflects real collaboration: chats, saved lessons, profile notes, and skills.';
    }
    positionTabInk();
  }

  function selectEvent(id) {
    state.selectedEventId = id;
    var events = (state.data && state.data.recent_events) || [];
    var diary = document.getElementById('cpDiary');
    var tl = document.getElementById('cpTimeline');
    if (diary) diary.innerHTML = renderDiaryCard(events);
    if (tl) {
      tl.innerHTML = renderTimeline(events);
      bindTimeline();
    }
  }

  function bindTimeline() {
    root.querySelectorAll('.cp-tl-item').forEach(function (el) {
      el.addEventListener('click', function () {
        selectEvent(el.getAttribute('data-event-id'));
      });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          selectEvent(el.getAttribute('data-event-id'));
        }
      });
    });
  }

  function bindControls() {
    var toggle = document.getElementById('companionLearning');
    if (toggle) {
      toggle.addEventListener('change', async function () {
        var on = !!toggle.checked;
        try {
          await Tomo.api('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ learning_enabled: on }),
          });
          var badge = document.getElementById('learnBadge');
          if (badge) {
            badge.textContent = on ? 'on' : 'off';
            badge.className = 'badge' + (on ? ' ok' : '');
          }
          var st = root.querySelector('.cp-status');
          if (st) st.classList.toggle('is-off', !on);
          if (window.Tomo && Tomo.toast) Tomo.toast(on ? 'Learning loop on' : 'Learning loop off');
        } catch (e) {
          toggle.checked = !on;
          if (window.Tomo && Tomo.toast) Tomo.toast('Could not update learning setting', 'error');
        }
      });
    }
    root.querySelectorAll('.cp-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        setTab(btn.getAttribute('data-tab'));
      });
    });
    bindTimeline();
    var more = document.getElementById('companionLoadMore');
    if (more) more.addEventListener('click', loadMore);
    window.addEventListener('resize', positionTabInk);
  }

  async function loadMore() {
    if (state.loadingMore || !state.nextBefore) return;
    state.loadingMore = true;
    try {
      var data = await Tomo.api(
        '/api/companion/events?limit=30&before=' + encodeURIComponent(state.nextBefore)
      );
      var events = (data && data.events) || [];
      if (state.data && events.length) {
        state.data.recent_events = (state.data.recent_events || []).concat(events);
        state.nextBefore = data.next_before || null;
        var diary = document.getElementById('cpDiary');
        var tl = document.getElementById('cpTimeline');
        if (diary) diary.innerHTML = renderDiaryCard(state.data.recent_events);
        if (tl) {
          tl.innerHTML = renderTimeline(state.data.recent_events);
          bindTimeline();
        }
        var foot = document.getElementById('companionLogFoot');
        if (foot && !state.nextBefore) foot.hidden = true;
      }
    } catch (e) {
      if (window.Tomo && Tomo.toast) Tomo.toast('Could not load more', 'error');
    } finally {
      state.loadingMore = false;
    }
  }

  async function boot() {
    try {
      var data = await Tomo.api('/api/companion');
      render(data);
    } catch (e) {
      root.innerHTML = '<div class="cp-empty">Could not load companion data.</div>';
    }
  }

  boot();
})();

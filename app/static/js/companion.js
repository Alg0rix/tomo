/* Companion page — bond, growth log, profile preview */
(function () {
  'use strict';

  var root = document.getElementById('companionRoot');
  if (!root) return;

  var state = {
    nextBefore: null,
    loadingMore: false,
  };

  function esc(s) {
    return Tomo.escapeHtml(s == null ? '' : s);
  }

  function fmtDate(ts) {
    if (!ts) return '';
    try {
      var d = new Date(Number(ts) * 1000);
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch (e) {
      return '';
    }
  }

  function monthLabel(ym) {
    if (!ym || ym.length < 7) return ym || '';
    var parts = ym.split('-');
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var mi = parseInt(parts[1], 10) - 1;
    return months[mi] || parts[1];
  }

  function renderEvent(ev) {
    var saved = !!ev.saved;
    var chips = [];
    if (ev.review_memory) chips.push('<span class="badge">memory</span>');
    if (ev.review_skills) chips.push('<span class="badge">skills</span>');
    if (saved) chips.push('<span class="badge ok">saved</span>');
    else chips.push('<span class="badge">idle</span>');

    var actions = (ev.actions || []).map(function (a) {
      return '<span class="companion-action mono">' + esc(a) + '</span>';
    }).join('');

    var body = saved
      ? '<div class="companion-diary">' + esc(ev.diary || ev.note || 'Lesson recorded.') + '</div>'
      : '<div class="companion-diary muted">' + esc(ev.note || 'Nothing to save.') + '</div>';

    return (
      '<article class="companion-log-card' + (saved ? '' : ' is-idle') + '">' +
        '<div class="companion-log-meta">' +
          '<time class="mono faint">' + esc(fmtDate(ev.created_at)) + '</time>' +
          (ev.agent_id ? '<span class="faint"> · ' + esc(ev.agent_id) + '</span>' : '') +
          (ev.reason ? '<span class="faint mono"> · ' + esc(ev.reason) + '</span>' : '') +
        '</div>' +
        '<div class="companion-log-chips">' + chips.join(' ') + '</div>' +
        body +
        (actions ? '<div class="companion-actions">' + actions + '</div>' : '') +
      '</article>'
    );
  }

  function renderGrowth(growth) {
    growth = growth || [];
    var max = 1;
    growth.forEach(function (g) {
      if ((g.events || 0) > max) max = g.events;
    });
    var bars = growth.map(function (g) {
      var h = Math.max(4, Math.round(((g.events || 0) / max) * 72));
      var sh = Math.max(0, Math.round(((g.saved || 0) / max) * 72));
      return (
        '<div class="companion-bar-col" title="' + esc(g.month) + ': ' + (g.events || 0) + ' reviews, ' + (g.saved || 0) + ' saved">' +
          '<div class="companion-bar-track">' +
            '<div class="companion-bar" style="height:' + h + 'px">' +
              (sh ? '<div class="companion-bar-saved" style="height:' + sh + 'px"></div>' : '') +
            '</div>' +
          '</div>' +
          '<div class="companion-bar-label mono">' + esc(monthLabel(g.month)) + '</div>' +
        '</div>'
      );
    }).join('');
    return '<div class="companion-growth-chart">' + bars + '</div>';
  }

  function render(data) {
    var bond = data.bond != null ? data.bond : 0;
    var stats = data.stats || {};
    var parts = data.bond_parts || {};
    var learning = !!data.learning_enabled;
    var events = data.recent_events || [];
    var preview = data.user_profile_preview || [];

    if (events.length) {
      state.nextBefore = events[events.length - 1].created_at || null;
    } else {
      state.nextBefore = null;
    }

    var profileHtml = preview.length
      ? '<ul class="companion-profile-list">' +
          preview.map(function (e) {
            return '<li>' + esc(e) + '</li>';
          }).join('') +
        '</ul>'
      : '<p class="faint">No profile notes yet. Preferences and corrections will appear here after learning saves.</p>';

    var logHtml = events.length
      ? events.map(renderEvent).join('')
      : '<div class="empty companion-empty">' +
          'No growth log yet. Keep Learning on and chat a few multi-step turns — Tomo will journal durable lessons here.' +
        '</div>';

    root.innerHTML =
      '<section class="card companion-hero">' +
        '<div class="card-body companion-hero-body">' +
          '<div class="companion-bond-block">' +
            '<div class="companion-bond-num">' + esc(bond) + '</div>' +
            '<div class="companion-bond-label">Bond</div>' +
            '<div class="companion-bond-bar" role="meter" aria-valuenow="' + bond + '" aria-valuemin="0" aria-valuemax="100">' +
              '<div class="companion-bond-fill" style="width:' + bond + '%"></div>' +
            '</div>' +
            '<p class="companion-bond-help faint">Bond is computed from user messages, saved learning reviews, USER.md size, library skills, and distinct active days (message or review timestamps).</p>' +
          '</div>' +
          '<div class="companion-hero-stats">' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(data.days_together || 0) + '</span><span class="companion-stat-k">Days together</span></div>' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(parts.chats || 0) + '</span><span class="companion-stat-k">Chats</span></div>' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(stats.events_saved || 0) + '</span><span class="companion-stat-k">Lessons saved</span></div>' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(stats.events_idle || 0) + '</span><span class="companion-stat-k">Idle reviews</span></div>' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(stats.skills_library || 0) + '</span><span class="companion-stat-k">Skills</span></div>' +
            '<div class="companion-stat"><span class="companion-stat-v mono">' + esc(stats.user_entries || 0) + '</span><span class="companion-stat-k">Profile notes</span></div>' +
          '</div>' +
          '<div class="companion-learn-row">' +
            '<div class="companion-learn-label">' +
              '<span>Learning loop</span>' +
              '<span class="badge ' + (learning ? 'ok' : '') + '" id="learnBadge">' + (learning ? 'on' : 'off') + '</span>' +
            '</div>' +
            '<label class="toggle companion-toggle">' +
              '<input type="checkbox" id="companionLearning" ' + (learning ? 'checked' : '') + '>' +
              '<span class="track"></span>' +
            '</label>' +
          '</div>' +
        '</div>' +
      '</section>' +

      '<section class="card companion-section">' +
        '<div class="card-head"><h3>Growth</h3></div>' +
        '<div class="card-body">' + renderGrowth(data.growth) +
          '<p class="faint companion-chart-legend">Bars = reviews · filled = saved lessons (last 12 months)</p>' +
        '</div>' +
      '</section>' +

      '<section class="card companion-section">' +
        '<div class="card-head"><h3>Growth log</h3></div>' +
        '<div class="card-body companion-log" id="companionLog">' + logHtml + '</div>' +
        '<div class="card-body companion-log-foot" id="companionLogFoot"' + (state.nextBefore && events.length >= 20 ? '' : ' hidden') + '>' +
          '<button type="button" class="btn ghost sm" id="companionLoadMore">Load more</button>' +
        '</div>' +
      '</section>' +

      '<section class="card companion-section">' +
        '<div class="card-head">' +
          '<h3>What I know</h3>' +
          '<div style="display:flex;gap:8px">' +
            '<a class="btn ghost sm" href="/skills">Skills</a>' +
            '<a class="btn ghost sm" href="/system#memory">Memory</a>' +
          '</div>' +
        '</div>' +
        '<div class="card-body">' + profileHtml + '</div>' +
      '</section>';

    bindControls();
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
          if (window.Tomo && Tomo.toast) Tomo.toast(on ? 'Learning loop on' : 'Learning loop off');
        } catch (e) {
          toggle.checked = !on;
          if (window.Tomo && Tomo.toast) Tomo.toast('Could not update learning setting', 'error');
        }
      });
    }
    var more = document.getElementById('companionLoadMore');
    if (more) {
      more.addEventListener('click', loadMore);
    }
  }

  async function loadMore() {
    if (state.loadingMore || !state.nextBefore) return;
    state.loadingMore = true;
    try {
      var data = await Tomo.api(
        '/api/companion/events?limit=30&before=' + encodeURIComponent(state.nextBefore)
      );
      var log = document.getElementById('companionLog');
      var events = (data && data.events) || [];
      if (log && events.length) {
        log.insertAdjacentHTML('beforeend', events.map(renderEvent).join(''));
      }
      state.nextBefore = data.next_before || null;
      var foot = document.getElementById('companionLogFoot');
      if (foot && !state.nextBefore) foot.hidden = true;
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
      root.innerHTML = '<div class="empty">Could not load companion data.</div>';
    }
  }

  boot();
})();

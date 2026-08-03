/* Token Monitor dashboard (heatmap + leaderboards + feed) */
(function () {
  "use strict";

  var root = document.getElementById("usagePage");
  if (!root) return;

  function esc(s) {
    return Tomo.escapeHtml(s == null ? "" : String(s));
  }

  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }

  function level(turns) {
    if (!turns) return 0;
    if (turns === 1) return 1;
    if (turns <= 3) return 2;
    if (turns <= 8) return 3;
    return 4;
  }

  function weekdayMon0(iso) {
    // JS: Sun=0 … map to Mon=0
    var d = new Date(iso + "T12:00:00Z");
    return (d.getUTCDay() + 6) % 7;
  }

  function renderHeat(days) {
    var host = document.getElementById("usageHeat");
    if (!host) return;
    days = days || [];
    if (!days.length) {
      host.innerHTML = '<div class="empty">No activity yet — run a chat turn.</div>';
      return;
    }

    // Pad so first column starts on Monday.
    var pad = weekdayMon0(days[0].date);
    var cells = [];
    for (var i = 0; i < pad; i++) {
      cells.push('<div class="uh-cell empty" aria-hidden="true"></div>');
    }
    days.forEach(function (d) {
      var lv = level(d.turns);
      var title =
        d.date +
        " · " +
        d.turns +
        " turn" +
        (d.turns === 1 ? "" : "s") +
        " · " +
        fmtNum(d.tokens) +
        " tokens";
      cells.push(
        '<div class="uh-cell l' +
          lv +
          '" title="' +
          esc(title) +
          '" data-date="' +
          esc(d.date) +
          '"></div>'
      );
    });
    host.innerHTML = '<div class="uh-grid">' + cells.join("") + "</div>";
  }

  function renderAgents(rows) {
    var host = document.getElementById("usageAgents");
    if (!host) return;
    if (!rows || !rows.length) {
      host.innerHTML = '<li class="faint">No agent activity yet.</li>';
      return;
    }
    host.innerHTML = rows
      .map(function (r, i) {
        return (
          '<li><span class="rank">' +
          (i + 1) +
          '</span><span class="name">' +
          esc(r.name || r.agent_id) +
          '</span><span class="meta">' +
          fmtNum(r.turns) +
          " turns · " +
          fmtNum(r.tokens) +
          " tok</span></li>"
        );
      })
      .join("");
  }

  function renderSessions(rows) {
    var host = document.getElementById("usageSessions");
    if (!host) return;
    if (!rows || !rows.length) {
      host.innerHTML = '<li class="faint">No session activity yet.</li>';
      return;
    }
    host.innerHTML = rows
      .map(function (r, i) {
        return (
          '<li><span class="rank">' +
          (i + 1) +
          '</span><a class="name" href="/sessions?s=' +
          encodeURIComponent(r.session_id) +
          '">' +
          esc(r.title || r.session_id) +
          '</a><span class="meta">' +
          fmtNum(r.turns) +
          " turns · " +
          fmtNum(r.tokens) +
          " tok</span></li>"
        );
      })
      .join("");
  }

  function renderFeed(rows) {
    var host = document.getElementById("usageFeed");
    if (!host) return;
    if (!rows || !rows.length) {
      host.innerHTML = '<li class="faint">No recent turns.</li>';
      return;
    }
    host.innerHTML = rows
      .map(function (r) {
        var when = r.created_at
          ? new Date(r.created_at * 1000).toLocaleString()
          : "";
        var preview = r.message_preview || "(no message)";
        return (
          '<li><div class="feed-top"><a href="/sessions?s=' +
          encodeURIComponent(r.session_id) +
          '">' +
          esc(r.title) +
          '</a><span class="faint mono">' +
          esc(r.agent_id || "—") +
          "</span><span class=\"faint\">" +
          esc(when) +
          '</span></div><div class="feed-body">' +
          esc(preview) +
          '</div><div class="feed-meta faint">' +
          fmtNum(r.turns) +
          " turn · " +
          fmtNum(r.tokens) +
          " tokens</div></li>"
        );
      })
      .join("");
  }

  function fillStats(summary) {
    var today = (summary && summary.today) || {};
    var week = (summary && summary.week) || {};
    var elT = root.querySelector('[data-stat="today"]');
    var elW = root.querySelector('[data-stat="week"]');
    var elA = root.querySelector('[data-stat="active"]');
    if (elW) {
      elW.textContent =
        fmtNum(week.turns) + " turns · " + fmtNum(week.tokens) + " tok";
    }
    if (elT) elT.textContent = fmtNum(today.turns) + " / " + fmtNum(today.tokens);
    if (elA) elA.textContent = String(summary.active_sessions_1h || 0);
  }

  async function load() {
    try {
      var data = await Tomo.api("/api/usage");
      fillStats(data.summary || {});
      renderHeat(data.heatmap || []);
      renderAgents(data.agents || []);
      renderSessions(data.sessions || []);
      renderFeed(data.activity || []);
    } catch (e) {
      Tomo.toast((e && e.message) || "Could not load usage", "err");
    }
  }

  load();
})();

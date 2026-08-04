/* Token Monitor dashboard — in / out first */
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

  function pctShare(a, b) {
    var t = (Number(a) || 0) + (Number(b) || 0);
    if (t <= 0) return { inPct: 50, outPct: 50 };
    var inPct = Math.round(((Number(a) || 0) / t) * 1000) / 10;
    return { inPct: inPct, outPct: Math.round((100 - inPct) * 10) / 10 };
  }

  function ratioLabel(prompt, completion) {
    var p = Number(prompt) || 0;
    var c = Number(completion) || 0;
    if (p <= 0 && c <= 0) return "—";
    if (c <= 0) return "∞ : 0";
    if (p <= 0) return "0 : ∞";
    var r = p / c;
    if (r >= 10) return r.toFixed(0) + " : 1";
    if (r >= 1) return r.toFixed(1).replace(/\.0$/, "") + " : 1";
    return "1 : " + (1 / r).toFixed(1).replace(/\.0$/, "");
  }

  function trackHtml(prompt, completion, turns) {
    var p = Number(prompt) || 0;
    var c = Number(completion) || 0;
    var share = pctShare(p, c);
    var turnsPart =
      turns != null
        ? '<span class="turns">' + fmtNum(turns) + " turn" + (turns === 1 ? "" : "s") + "</span>"
        : "";
    return (
      '<div class="usage-track">' +
      '<div class="usage-track-bar" role="img" aria-label="in ' +
      fmtNum(p) +
      ", out " +
      fmtNum(c) +
      '">' +
      '<span class="seg-in" style="width:' +
      share.inPct +
      '%"></span>' +
      '<span class="seg-out" style="width:' +
      share.outPct +
      '%"></span>' +
      "</div>" +
      '<div class="usage-track-nums">' +
      '<span class="io-in">↓ ' +
      fmtNum(p) +
      "</span>" +
      '<span class="io-out">↑ ' +
      fmtNum(c) +
      "</span>" +
      '<span class="io-total">' +
      fmtNum(p + c) +
      " total</span>" +
      turnsPart +
      "</div></div>"
    );
  }

  function chipsHtml(prompt, completion, extra) {
    return (
      '<span class="io-chip is-in"><span class="io-k">In</span>' +
      fmtNum(prompt) +
      '</span><span class="io-chip is-out"><span class="io-k">Out</span>' +
      fmtNum(completion) +
      "</span>" +
      (extra || "")
    );
  }

  function level(turns) {
    if (!turns) return 0;
    if (turns === 1) return 1;
    if (turns <= 3) return 2;
    if (turns <= 8) return 3;
    return 4;
  }

  function weekdayMon0(iso) {
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
        " · in " +
        fmtNum(d.prompt_tokens) +
        " / out " +
        fmtNum(d.completion_tokens) +
        " (" +
        fmtNum(d.tokens) +
        " total)";
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
          '</span><div class="row-main"><div class="row-top"><span class="name">' +
          esc(r.name || r.agent_id) +
          "</span></div>" +
          trackHtml(r.prompt_tokens, r.completion_tokens, r.turns) +
          "</div></li>"
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
          '</span><div class="row-main"><div class="row-top"><a class="name" href="/sessions?s=' +
          encodeURIComponent(r.session_id) +
          '">' +
          esc(r.title || r.session_id) +
          "</a></div>" +
          trackHtml(r.prompt_tokens, r.completion_tokens, r.turns) +
          "</div></li>"
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
        var extra =
          '<span class="io-chip is-muted">' +
          fmtNum(r.turns) +
          " turn</span>" +
          (when
            ? '<span class="io-chip is-muted">' + esc(when) + "</span>"
            : "");
        return (
          '<li><div class="feed-top"><a href="/sessions?s=' +
          encodeURIComponent(r.session_id) +
          '">' +
          esc(r.title) +
          '</a><span class="faint mono">' +
          esc(r.agent_id || "—") +
          '</span></div><div class="feed-body">' +
          esc(preview) +
          '</div><div class="feed-meta">' +
          chipsHtml(r.prompt_tokens, r.completion_tokens, extra) +
          "</div></li>"
        );
      })
      .join("");
  }

  function setText(sel, text) {
    var el = root.querySelector(sel);
    if (el) el.textContent = text;
  }

  function fillStats(summary) {
    var today = (summary && summary.today) || {};
    var week = (summary && summary.week) || {};
    var wIn = week.prompt_tokens || 0;
    var wOut = week.completion_tokens || 0;
    var tIn = today.prompt_tokens || 0;
    var tOut = today.completion_tokens || 0;

    setText('[data-stat="week-in"]', fmtNum(wIn));
    setText('[data-stat="week-out"]', fmtNum(wOut));
    setText('[data-stat="today-in"]', fmtNum(tIn));
    setText('[data-stat="today-out"]', fmtNum(tOut));
    setText('[data-stat="week-ratio"]', ratioLabel(wIn, wOut));
    setText('[data-stat="week-turns"]', fmtNum(week.turns));
    setText('[data-stat="week-total"]', fmtNum(week.tokens));
    setText('[data-stat="today-turns"]', fmtNum(today.turns));
    setText(
      '[data-stat="active"]',
      String((summary && summary.active_sessions_1h) || 0)
    );

    var share = pctShare(wIn, wOut);
    var barIn = root.querySelector('[data-bar="in"]');
    var barOut = root.querySelector('[data-bar="out"]');
    if (barIn) barIn.style.width = share.inPct + "%";
    if (barOut) barOut.style.width = share.outPct + "%";
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

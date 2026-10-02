/* context_usage.js — context window breakdown popover for chat threads */
(function () {
  "use strict";

  function esc(s) {
    return Tomo.escapeHtml(s);
  }

  function fmtTokens(n) {
    n = Number(n) || 0;
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }

  function contextUrl(wrap) {
    var sid = wrap.dataset.sessionId;
    var aid = wrap.dataset.agentId;
    var uid = wrap.dataset.userId || "web";
    if (sid) return "/api/sessions/" + encodeURIComponent(sid) + "/context";
    if (aid) {
      return "/api/agents/" + encodeURIComponent(aid) + "/context?user_id=" + encodeURIComponent(uid);
    }
    return "";
  }

  function initContextUsage(wrap) {
    if (!wrap || wrap.dataset.ctxInit === "1") return;
    if (wrap._tomoContextUsageDestroy) wrap._tomoContextUsageDestroy();
    var trigger = wrap.querySelector(".ctx-usage-trigger");
    if (!trigger) return;
    wrap.dataset.ctxInit = "1";

    var popover = null;
    var open = false;
    var disposed = false;
    var refreshRevision = 0;

    function closePopover() {
      open = false;
      if (popover) popover.classList.add("hidden");
      trigger.setAttribute("aria-expanded", "false");
    }

    function ensurePopover() {
      if (popover) return popover;
      popover = document.createElement("div");
      popover.className = "ctx-usage-popover hidden";
      popover.setAttribute("role", "dialog");
      popover.setAttribute("aria-label", "Context Usage");
      popover.innerHTML =
        '<div class="ctx-pop-head">' +
          '<span class="ctx-pop-title">Context Usage</span>' +
          '<button type="button" class="ctx-pop-close" aria-label="Close">\u2715</button>' +
        '</div>' +
        '<div class="ctx-pop-summary">' +
          '<span class="ctx-pop-pct"></span>' +
          '<span class="ctx-pop-tokens faint mono"></span>' +
        '</div>' +
        '<div class="ctx-pop-bar" aria-hidden="true"></div>' +
        '<ul class="ctx-pop-legend"></ul>' +
        '<p class="ctx-pop-budget ctx-usage-note"></p>' +
        '<div class="ctx-pop-usage"></div>';
      // Mount on the visible composer card so the popover sits over the ring.
      var host =
        trigger.closest(".composer-shell") ||
        trigger.closest(".composer") ||
        wrap.querySelector(".composer") ||
        wrap;
      host.appendChild(popover);
      popover.querySelector(".ctx-pop-close").addEventListener("click", closePopover);
      return popover;
    }

    function renderBar(sections, used, limit) {
      var bar = popover.querySelector(".ctx-pop-bar");
      if (!used || !sections.length) {
        bar.innerHTML = '<div class="ctx-seg ctx-seg-empty"></div>';
        return;
      }
      bar.innerHTML = sections.map(function (s) {
        var w = Math.max(0.5, (s.tokens / used) * 100);
        return '<div class="ctx-seg" style="width:' + w + '%;background:' + esc(s.color) + '" title="' + esc(s.label) + '"></div>';
      }).join("");
    }

    function renderLegend(sections) {
      var list = popover.querySelector(".ctx-pop-legend");
      list.innerHTML = sections.map(function (s) {
        return '<li class="ctx-leg-row">' +
          '<span class="ctx-swatch" style="background:' + esc(s.color) + '"></span>' +
          '<span class="ctx-leg-label">' + esc(s.label) + '</span>' +
          '<span class="ctx-leg-val mono">' + esc(fmtTokens(s.tokens)) + '</span>' +
        '</li>';
      }).join("");
    }

    function renderUsage(usage) {
      var el = popover.querySelector(".ctx-pop-usage");
      var html = '<div class="ctx-pop-title">Recorded session usage</div>';
      if (!usage || !usage.recorded_turns) {
        el.innerHTML = html + '<p class="ctx-usage-note">Available after a new response completes.</p>';
        return;
      }
      var estimated = usage.estimated_rounds > 0 ? "~" : "";
      var hasCache = usage.cache_hit_rate != null;
      var rows = [
        ["Input tokens", estimated + fmtTokens(usage.prompt_tokens)],
        ["Output tokens", estimated + fmtTokens(usage.completion_tokens)],
        ["Cached input", hasCache ? fmtTokens(usage.cached_tokens) : "N/A"],
        ["Cache hit rate", hasCache ? usage.cache_hit_rate + "%" : "N/A"],
      ];
      if (usage.reasoning_reported_rounds) rows.push(["Reasoning tokens", fmtTokens(usage.reasoning_tokens)]);
      rows.push(["LLM calls", fmtTokens(usage.llm_rounds)]);
      rows.push(["Tool calls", fmtTokens(usage.tool_calls)]);
      if (usage.last_elapsed_ms != null) {
        rows.push(["Last turn duration", (usage.last_elapsed_ms / 1000).toFixed(1) + "s"]);
      }
      html += '<dl class="ctx-usage-stats">' + rows.map(function (row) {
        return '<div><dt>' + esc(row[0]) + '</dt><dd class="mono">' + esc(row[1]) + '</dd></div>';
      }).join("") + '</dl>';
      var note = hasCache
        ? "Cached / input tokens for " + usage.cache_reported_rounds + " LLM calls with cache details."
        : "Provider did not report cache details.";
      if (usage.estimated_rounds) note += " ~ includes estimated token usage.";
      html += '<p class="ctx-usage-note">' + esc(note) + ' Totals include recorded delegate responses. Reasoning is part of output tokens.</p>';
      el.innerHTML = html;
    }

    function updateTrigger(data) {
      var pct = data.percent || 0;
      var pctEl = trigger.querySelector(".ctx-usage-pct");
      var ring = trigger.querySelector(".ctx-usage-ring");
      if (pctEl) pctEl.textContent = pct + "%";
      if (ring) {
        ring.style.setProperty("--ctx-pct", String(pct));
        ring.classList.toggle("warn", pct >= 75);
        ring.classList.toggle("full", pct >= 90);
      }
      trigger.title = fmtTokens(data.used) + " / " + fmtTokens(data.limit) + " tokens";
    }

    function render(data) {
      ensurePopover();
      var pct = data.percent || 0;
      popover.querySelector(".ctx-pop-pct").textContent = pct + "% Full";
      popover.querySelector(".ctx-pop-tokens").textContent =
        "~" + fmtTokens(data.used) + " / " + fmtTokens(data.limit) + " Tokens";
      renderBar(data.sections || [], data.used || 0, data.limit || 0);
      renderLegend(data.sections || []);
      renderUsage(data.usage);
      popover.querySelector(".ctx-pop-budget").textContent = data.blocked
        ? (data.compaction_error || "The latest request and instructions cannot fit this model.")
        : (data.compressed ? "Older conversation is compacted to fit this model. " : "")
          + (data.prompt_budget != null ? "Prompt budget: " + fmtTokens(data.prompt_budget) + " tokens; remaining space reserved for the reply and token estimates." : "");
      updateTrigger(data);
    }

    function refresh() {
      var revision = ++refreshRevision;
      var url = contextUrl(wrap);
      if (!url) return;
      Tomo.api(url).then(function (data) {
        if (!disposed && revision === refreshRevision && contextUrl(wrap) === url && data) render(data);
      }).catch(function () {});
    }

    wrap._tomoContextUsageRefresh = refresh;

    function onTriggerClick(e) {
      e.stopPropagation();
      ensurePopover();
      if (open) {
        closePopover();
        return;
      }
      open = true;
      trigger.setAttribute("aria-expanded", "true");
      popover.classList.remove("hidden");
      refresh();
    }

    function onDocumentClick(e) {
      if (!open || !popover) return;
      if (popover.contains(e.target) || trigger.contains(e.target)) return;
      closePopover();
    }

    function onDocumentKeydown(e) {
      if (e.key === "Escape") closePopover();
    }

    function onChatCleared() {
      refresh();
    }

    trigger.addEventListener("click", onTriggerClick);
    document.addEventListener("click", onDocumentClick);
    document.addEventListener("keydown", onDocumentKeydown);
    wrap.addEventListener("tomo:turn-end", refresh);
    wrap.addEventListener("tomo:chat-cleared", onChatCleared);

    wrap._tomoContextUsageDestroy = function () {
      disposed = true;
      trigger.removeEventListener("click", onTriggerClick);
      document.removeEventListener("click", onDocumentClick);
      document.removeEventListener("keydown", onDocumentKeydown);
      wrap.removeEventListener("tomo:turn-end", refresh);
      wrap.removeEventListener("tomo:chat-cleared", onChatCleared);
      if (popover) popover.remove();
      popover = null;
      delete wrap._tomoContextUsageDestroy;
      delete wrap._tomoContextUsageRefresh;
      delete wrap.dataset.ctxInit;
    };

    refresh();
  }

  window.TomoContextUsage = {
    init: initContextUsage,
    refresh: function (wrap) {
      if (wrap && wrap._tomoContextUsageRefresh) wrap._tomoContextUsageRefresh();
    },
    destroy: function (wrap) {
      if (wrap && wrap._tomoContextUsageDestroy) wrap._tomoContextUsageDestroy();
    },
  };
})();

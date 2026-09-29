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
        '<ul class="ctx-pop-legend"></ul>';
      // Mount on .composer (not .composer-shell / footer) so overflow never clips.
      var host =
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
      updateTrigger(data);
    }

    function refresh() {
      var url = contextUrl(wrap);
      if (!url) return;
      Tomo.api(url).then(function (data) {
        if (!disposed && contextUrl(wrap) === url && data) render(data);
      }).catch(function () {});
    }

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
      delete wrap.dataset.ctxInit;
    };

    refresh();
  }

  window.TomoContextUsage = {
    init: initContextUsage,
    destroy: function (wrap) {
      if (wrap && wrap._tomoContextUsageDestroy) wrap._tomoContextUsageDestroy();
    },
  };
})();

/* hitl_ui.js — approval / clarify cards shared by live + resume chat streams. */
(function () {
  "use strict";

  function esc(s) {
    return window.Tomo && Tomo.escapeHtml ? Tomo.escapeHtml(s) : String(s == null ? "" : s);
  }

  function buildApprovalCard(d) {
    var card = document.createElement("div");
    card.className = "hitl-card approval-card";
    card.dataset.id = d.id || "";
    var findings = (d.findings || [])
      .map(function (f) {
        return "<li>" + esc(f.description || f.kind || "") + "</li>";
      })
      .join("");
    var preview = "";
    try {
      preview =
        typeof d.args_preview === "string"
          ? d.args_preview
          : JSON.stringify(d.args_preview || {}, null, 2);
    } catch (_) {
      preview = String(d.args_preview || "");
    }
    var choices = d.choices || ["once", "session", "always", "deny"];
    var labels = { once: "Once", session: "Session", always: "Always", deny: "Deny" };
    var btns = choices
      .map(function (c) {
        return (
          '<button type="button" class="hitl-btn" data-choice="' +
          esc(c) +
          '">' +
          esc(labels[c] || c) +
          "</button>"
        );
      })
      .join("");
    card.innerHTML =
      '<div class="hitl-title">Approval required · ' +
      esc(d.tool || "tool") +
      "</div>" +
      '<div class="hitl-desc">' +
      esc(d.description || "") +
      "</div>" +
      (findings ? '<ul class="hitl-findings">' + findings + "</ul>" : "") +
      '<pre class="hitl-preview">' +
      esc(preview).slice(0, 800) +
      "</pre>" +
      '<div class="hitl-actions">' +
      btns +
      "</div>";
    card.querySelectorAll(".hitl-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var choice = btn.getAttribute("data-choice");
        card.classList.add("resolved");
        card.querySelectorAll(".hitl-btn").forEach(function (b) {
          b.disabled = true;
        });
        fetch("/api/approvals/" + encodeURIComponent(d.id), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ choice: choice }),
        }).catch(function () {});
      });
    });
    return card;
  }

  function buildClarifyCard(d) {
    var card = document.createElement("div");
    card.className = "hitl-card clarify-card";
    card.dataset.id = d.id || "";
    var choices = d.choices || [];
    var btns = choices
      .map(function (c) {
        return (
          '<button type="button" class="hitl-btn" data-answer="' +
          esc(c) +
          '">' +
          esc(c) +
          "</button>"
        );
      })
      .join("");
    card.innerHTML =
      '<div class="hitl-title">Question</div>' +
      '<div class="hitl-desc">' +
      esc(d.question || "") +
      "</div>" +
      '<div class="hitl-actions">' +
      btns +
      "</div>" +
      '<div class="hitl-other">' +
      '<input type="text" class="hitl-input" placeholder="Other…" />' +
      '<button type="button" class="hitl-btn hitl-send">Send</button>' +
      "</div>";
    function submit(answer) {
      if (!answer) return;
      card.classList.add("resolved");
      card.querySelectorAll("button,input").forEach(function (el) {
        el.disabled = true;
      });
      fetch("/api/clarify/" + encodeURIComponent(d.id), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: answer }),
      }).catch(function () {});
    }
    card.querySelectorAll(".hitl-btn[data-answer]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        submit(btn.getAttribute("data-answer") || "");
      });
    });
    var sendHitlBtn = card.querySelector(".hitl-send");
    var inputEl = card.querySelector(".hitl-input");
    if (sendHitlBtn && inputEl) {
      sendHitlBtn.addEventListener("click", function () {
        submit(inputEl.value.trim());
      });
      inputEl.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          submit(inputEl.value.trim());
        }
      });
    }
    return card;
  }

  /** Append HITL card once (dedupe by data-id). */
  function showCard(kind, d, host, scrollEl) {
    var id = d && d.id ? String(d.id) : "";
    if (!id || !host) return null;
    var safe = id.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    var existing = host.querySelector('.hitl-card[data-id="' + safe + '"]');
    if (existing) return existing;
    var card = kind === "clarify" ? buildClarifyCard(d) : buildApprovalCard(d);
    host.appendChild(card);
    if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
    return card;
  }

  /**
   * Wire approval_required / clarify_required on an EventSource-like stream.
   * opts: { turn, scroll, onEvent?: fn, clearPending?: fn, setBusy?: fn }
   */
  function bindStream(es, opts) {
    opts = opts || {};
    function handle(kind, e) {
      if (opts.onEvent) opts.onEvent();
      var d = {};
      try {
        d = JSON.parse(e.data || "{}");
      } catch (_) {}
      if (opts.clearPending) opts.clearPending();
      showCard(kind, d, opts.turn, opts.scroll);
      if (opts.setBusy) opts.setBusy();
    }
    es.addEventListener("approval_required", function (e) {
      handle("approval", e);
    });
    es.addEventListener("clarify_required", function (e) {
      handle("clarify", e);
    });
  }

  /** Fetch open HITL for a session and render into host. Resolves true if work remains. */
  function rehydrate(sessionId, host, scrollEl) {
    if (!sessionId) return Promise.resolve(false);
    return fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/pending", {
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data) return false;
        var any = false;
        (data.approvals || []).forEach(function (d) {
          if (showCard("approval", d, host, scrollEl)) any = true;
        });
        (data.clarifies || []).forEach(function (d) {
          if (showCard("clarify", d, host, scrollEl)) any = true;
        });
        return !!(any || data.active_turn);
      })
      .catch(function () {
        return false;
      });
  }

  window.TomoHitl = {
    buildApprovalCard: buildApprovalCard,
    buildClarifyCard: buildClarifyCard,
    showCard: showCard,
    bindStream: bindStream,
    rehydrate: rehydrate,
  };
})();

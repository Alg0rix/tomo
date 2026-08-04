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
    card.setAttribute("role", "region");
    card.setAttribute("aria-label", "Tool approval required");
    var findings = (d.findings || [])
      .map(function (f) {
        var kind = f.kind ? '<span class="hitl-finding-kind">' + esc(f.kind) + "</span> " : "";
        return "<li>" + kind + esc(f.description || f.kind || "") + "</li>";
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
    var labels = {
      once: "Allow once",
      session: "This session",
      always: "Always allow",
      deny: "Deny",
    };
    var variants = {
      once: "hitl-btn hitl-btn-primary",
      session: "hitl-btn hitl-btn-secondary",
      always: "hitl-btn hitl-btn-ghost",
      deny: "hitl-btn hitl-btn-danger",
    };
    var allow = choices
      .filter(function (c) {
        return c !== "deny";
      })
      .map(function (c) {
        return (
          '<button type="button" class="' +
          (variants[c] || "hitl-btn hitl-btn-secondary") +
          '" data-choice="' +
          esc(c) +
          '">' +
          esc(labels[c] || c) +
          "</button>"
        );
      })
      .join("");
    var deny = choices.indexOf("deny") >= 0
      ? '<button type="button" class="hitl-btn hitl-btn-danger" data-choice="deny">' +
        esc(labels.deny) +
        "</button>"
      : "";
    card.innerHTML =
      '<div class="hitl-rail" aria-hidden="true"></div>' +
      '<div class="hitl-body">' +
      '<header class="hitl-hd">' +
      '<span class="hitl-kicker">Permission</span>' +
      '<span class="hitl-tool">' +
      esc(d.tool || "tool") +
      "</span>" +
      "</header>" +
      (d.description
        ? '<p class="hitl-desc">' + esc(d.description) + "</p>"
        : "") +
      (findings ? '<ul class="hitl-findings">' + findings + "</ul>" : "") +
      (preview
        ? '<pre class="hitl-preview" tabindex="0">' +
          esc(preview).slice(0, 800) +
          "</pre>"
        : "") +
      '<div class="hitl-actions">' +
      '<div class="hitl-allow">' +
      allow +
      "</div>" +
      (deny ? '<div class="hitl-deny">' + deny + "</div>" : "") +
      "</div>" +
      "</div>";
    card.querySelectorAll(".hitl-btn[data-choice]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var choice = btn.getAttribute("data-choice");
        card.classList.add("resolved");
        card.dataset.choice = choice || "";
        card.querySelectorAll(".hitl-btn").forEach(function (b) {
          b.disabled = true;
          if (b === btn) b.classList.add("is-chosen");
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
    card.setAttribute("role", "region");
    card.setAttribute("aria-label", "Clarification needed");
    var choices = d.choices || [];
    var btns = choices
      .map(function (c) {
        return (
          '<button type="button" class="hitl-btn hitl-btn-secondary" data-answer="' +
          esc(c) +
          '">' +
          esc(c) +
          "</button>"
        );
      })
      .join("");
    card.innerHTML =
      '<div class="hitl-rail" aria-hidden="true"></div>' +
      '<div class="hitl-body">' +
      '<header class="hitl-hd">' +
      '<span class="hitl-kicker">Clarify</span>' +
      "</header>" +
      '<p class="hitl-desc hitl-question">' +
      esc(d.question || "") +
      "</p>" +
      (btns ? '<div class="hitl-actions"><div class="hitl-allow">' + btns + "</div></div>" : "") +
      '<div class="hitl-other">' +
      '<input type="text" class="hitl-input" placeholder="Or type a reply…" aria-label="Custom answer" />' +
      '<button type="button" class="hitl-btn hitl-btn-primary hitl-send">Send</button>' +
      "</div>" +
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
    if (scrollEl) {
      if (window.Tomo && Tomo.scrollToBottomInstant) {
        Tomo.scrollToBottomInstant(scrollEl);
      } else {
        var prev = scrollEl.style.scrollBehavior;
        scrollEl.style.scrollBehavior = 'auto';
        scrollEl.scrollTop = scrollEl.scrollHeight;
        scrollEl.style.scrollBehavior = prev;
      }
    }
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

/* modals.js — training + test detail modals */
(function () {
  "use strict";

  var SAMPLE_TRAINING = [
    { messages: [{ role: "user", content: "Route this to ops" }, { role: "assistant", content: "@ops please handle deployment" }] },
    { messages: [{ role: "user", content: "Summarize the incident" }, { role: "assistant", content: "Brief summary with severity and next steps." }] },
  ];

  var SAMPLE_TESTS = [
    { id: "routing_l1_01", name: "routing · L1 · delegate", status: "pass", prompt: "Ask ops to restart nginx", expected: "@ops restart nginx", actual: "@ops restart nginx" },
    { id: "routing_l2_03", name: "routing · L2 · clarify", status: "warn", prompt: "Fix the server", expected: "clarifying question", actual: "I'll restart everything now." },
    { id: "tools_l1_02", name: "tools · L1 · read file", status: "fail", prompt: "Show config.toml", expected: "tool: read_file", actual: "Here's a guess at your config..." },
  ];

  function openModal(el) {
    if (!el) return;
    el.classList.remove("hidden");
    el.setAttribute("aria-hidden", "false");
  }

  function closeModal(el) {
    if (!el) return;
    el.classList.add("hidden");
    el.setAttribute("aria-hidden", "true");
  }

  function bindClose(modal, attr) {
    if (!modal) return;
    modal.querySelectorAll("[data-" + attr + "]").forEach(function (btn) {
      btn.addEventListener("click", function () { closeModal(modal); });
    });
  }

  function openTraining() {
    var modal = document.getElementById("trainingModal");
    var ta = document.getElementById("trainingTextarea");
    if (!modal || !ta) return;
    ta.value = JSON.stringify(SAMPLE_TRAINING, null, 2);
    openModal(modal);
  }

  function copyTraining() {
    var ta = document.getElementById("trainingTextarea");
    if (!ta) return;
    var lines = SAMPLE_TRAINING.map(function (row) { return JSON.stringify(row); }).join("\n");
    navigator.clipboard.writeText(lines).then(function () {
      if (window.Tomo && Tomo.toast) Tomo.toast("Copied JSONL", "ok");
    }).catch(function () {
      if (window.Tomo && Tomo.toast) Tomo.toast("Copy failed", "err");
    });
  }

  function renderTestDetail(test) {
    var detail = document.getElementById("testModalDetail");
    if (!detail || !test) return;
    var badge = test.status === "pass" ? "ok" : (test.status === "fail" ? "rose" : "amber");
    detail.innerHTML =
      "<div><span class=\"badge " + badge + " sm\">" + test.status + "</span> " +
      "<strong style=\"margin-left:6px\">" + test.name + "</strong></div>" +
      "<div class=\"setting-group-label\" style=\"margin-top:14px\">Prompt</div><pre>" + escapeHtml(test.prompt) + "</pre>" +
      "<div class=\"setting-group-label\" style=\"margin-top:10px\">Expected</div><pre>" + escapeHtml(test.expected) + "</pre>" +
      "<div class=\"setting-group-label\" style=\"margin-top:10px\">Actual</div><pre>" + escapeHtml(test.actual) + "</pre>";
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function openTests(runId) {
    var modal = document.getElementById("testModal");
    var list = document.getElementById("testModalList");
    var title = document.getElementById("testModalTitle");
    if (!modal || !list) return;
    if (title) title.textContent = runId ? "Tests · " + runId : "Test details";
    list.innerHTML = "";
    SAMPLE_TESTS.forEach(function (test, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "modal-test-item" + (i === 0 ? " active" : "");
      btn.textContent = test.name;
      btn.addEventListener("click", function () {
        list.querySelectorAll(".modal-test-item").forEach(function (el) { el.classList.remove("active"); });
        btn.classList.add("active");
        renderTestDetail(test);
      });
      list.appendChild(btn);
    });
    renderTestDetail(SAMPLE_TESTS[0]);
    openModal(modal);
  }

  bindClose(document.getElementById("trainingModal"), "close-training");
  bindClose(document.getElementById("testModal"), "close-test");

  var trainBtn = document.getElementById("openTrainingModal");
  if (trainBtn) trainBtn.addEventListener("click", openTraining);

  var copyBtn = document.getElementById("copyTrainingBtn");
  if (copyBtn) copyBtn.addEventListener("click", copyTraining);

  var testBtn = document.getElementById("openTestModal");
  if (testBtn) {
    testBtn.addEventListener("click", function () {
      openTests(testBtn.getAttribute("data-run") || "");
    });
  }

  window.TomoModals = { openTraining: openTraining, openTests: openTests };
})();

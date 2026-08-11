/**
 * Injected into pages Tomo controls.
 * Shows a Perplexity-style top wave bar while the agent is driving this tab.
 */
(function () {
  "use strict";

  var ROOT_ID = "tomo-browser-control-root";
  var STYLE_ID = "tomo-browser-control-style";
  var hideTimer = null;
  var refCount = 0;

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = [
      "#" + ROOT_ID + " {",
      "  all: initial;",
      "  position: fixed !important;",
      "  top: 0 !important; left: 0 !important; right: 0 !important;",
      "  z-index: 2147483646 !important;",
      "  pointer-events: none !important;",
      "  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif !important;",
      "}",
      "#" + ROOT_ID + " .tomo-wave-bar {",
      "  position: relative;",
      "  height: 3px;",
      "  overflow: hidden;",
      "  background: linear-gradient(90deg, #1a5cff, #3d8bfd, #7c5cff, #3d8bfd, #1a5cff);",
      "  background-size: 200% 100%;",
      "  animation: tomoWaveShift 1.4s linear infinite;",
      "  box-shadow: 0 0 12px rgba(61,139,253,0.55), 0 2px 10px rgba(61,139,253,0.25);",
      "}",
      "#" + ROOT_ID + " .tomo-wave-bar::after {",
      "  content: '';",
      "  position: absolute;",
      "  inset: 0;",
      "  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.55), transparent);",
      "  background-size: 40% 100%;",
      "  animation: tomoWaveShine 1.1s ease-in-out infinite;",
      "}",
      "#" + ROOT_ID + " .tomo-wave-glow {",
      "  height: 28px;",
      "  background: linear-gradient(180deg, rgba(61,139,253,0.18), transparent);",
      "  pointer-events: none;",
      "}",
      "#" + ROOT_ID + " .tomo-pill {",
      "  position: absolute;",
      "  top: 10px;",
      "  left: 50%;",
      "  transform: translateX(-50%);",
      "  display: inline-flex;",
      "  align-items: center;",
      "  gap: 8px;",
      "  padding: 6px 12px;",
      "  border-radius: 999px;",
      "  background: rgba(12, 16, 28, 0.88);",
      "  color: #e8f0ff !important;",
      "  font-size: 12px !important;",
      "  font-weight: 600 !important;",
      "  line-height: 1.2 !important;",
      "  letter-spacing: 0.01em;",
      "  border: 1px solid rgba(61,139,253,0.45);",
      "  box-shadow: 0 8px 28px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.04) inset;",
      "  backdrop-filter: blur(10px);",
      "  -webkit-backdrop-filter: blur(10px);",
      "  white-space: nowrap;",
      "  max-width: min(90vw, 420px);",
      "  overflow: hidden;",
      "  text-overflow: ellipsis;",
      "}",
      "#" + ROOT_ID + " .tomo-pill-dot {",
      "  width: 8px; height: 8px; border-radius: 50%;",
      "  background: #3d8bfd;",
      "  box-shadow: 0 0 0 3px rgba(61,139,253,0.28);",
      "  animation: tomoDotPulse 1.1s ease-in-out infinite;",
      "  flex-shrink: 0;",
      "}",
      "#" + ROOT_ID + " .tomo-pill-live {",
      "  font-size: 10px !important;",
      "  font-weight: 700 !important;",
      "  letter-spacing: 0.08em;",
      "  color: #8ec5ff !important;",
      "  background: rgba(61,139,253,0.18);",
      "  border: 1px solid rgba(61,139,253,0.35);",
      "  border-radius: 999px;",
      "  padding: 2px 6px;",
      "}",
      "#" + ROOT_ID + " .tomo-pill-label {",
      "  overflow: hidden;",
      "  text-overflow: ellipsis;",
      "  color: #e8f0ff !important;",
      "  font-size: 12px !important;",
      "}",
      "@keyframes tomoWaveShift {",
      "  0% { background-position: 0% 50%; }",
      "  100% { background-position: 200% 50%; }",
      "}",
      "@keyframes tomoWaveShine {",
      "  0% { transform: translateX(-120%); }",
      "  100% { transform: translateX(320%); }",
      "}",
      "@keyframes tomoDotPulse {",
      "  0%, 100% { transform: scale(1); opacity: 1; }",
      "  50% { transform: scale(1.2); opacity: 0.7; }",
      "}",
      "@media (prefers-reduced-motion: reduce) {",
      "  #" + ROOT_ID + " .tomo-wave-bar,",
      "  #" + ROOT_ID + " .tomo-wave-bar::after,",
      "  #" + ROOT_ID + " .tomo-pill-dot { animation: none !important; }",
      "}",
    ].join("\n");
    (document.documentElement || document.head || document.body).appendChild(style);
  }

  function ensureRoot() {
    ensureStyles();
    var root = document.getElementById(ROOT_ID);
    if (root) return root;
    root = document.createElement("div");
    root.id = ROOT_ID;
    root.setAttribute("data-tomo-control", "1");
    root.innerHTML =
      '<div class="tomo-wave-bar"></div>' +
      '<div class="tomo-wave-glow"></div>' +
      '<div class="tomo-pill">' +
        '<span class="tomo-pill-dot"></span>' +
        '<span class="tomo-pill-live">LIVE</span>' +
        '<span class="tomo-pill-label">Tomo is controlling this tab</span>' +
      "</div>";
    var parent = document.documentElement || document.body;
    parent.appendChild(root);
    return root;
  }

  function show(label) {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    refCount += 1;
    var root = ensureRoot();
    root.style.display = "block";
    var text = root.querySelector(".tomo-pill-label");
    if (text) {
      text.textContent = label
        ? "Tomo · " + String(label)
        : "Tomo is controlling this tab";
    }
  }

  function hide(force) {
    if (force) {
      refCount = 0;
    } else {
      refCount = Math.max(0, refCount - 1);
    }
    if (refCount > 0) return;
    var root = document.getElementById(ROOT_ID);
    if (!root) return;
    // brief linger so short tools still read as "controlled"
    hideTimer = setTimeout(function () {
      if (refCount > 0) return;
      root.style.display = "none";
      hideTimer = null;
    }, 280);
  }

  function destroy() {
    refCount = 0;
    if (hideTimer) clearTimeout(hideTimer);
    var root = document.getElementById(ROOT_ID);
    if (root) root.remove();
    var style = document.getElementById(STYLE_ID);
    if (style) style.remove();
  }

  // Idempotent install marker for executeScript re-entry
  window.__tomoBrowserControl = {
    show: show,
    hide: hide,
    destroy: destroy,
    version: 1,
  };

  if (!window.__tomoBrowserControlListener) {
    window.__tomoBrowserControlListener = true;
    chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "TOMO_CONTROL_START") {
        show(msg.label || msg.activity || "");
        sendResponse({ ok: true });
        return true;
      }
      if (msg.type === "TOMO_CONTROL_END") {
        hide(!!msg.force);
        sendResponse({ ok: true });
        return true;
      }
      if (msg.type === "TOMO_CONTROL_PING") {
        sendResponse({ ok: true, version: 1 });
        return true;
      }
    });
  }
})();

/* artifacts.js — session artifacts + Darkroom artifact side panel. */
(function (global) {
  "use strict";

  var IMG_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i;
  var VID_EXT = /\.(mp4|webm|mov|m4v)$/i;
  var AUD_EXT = /\.(mp3|wav|ogg|flac|aac|m4a)$/i;
  var HTML_EXT = /\.html?$/i;
  var MD_EXT = /\.(md|markdown)$/i;
  var CSV_EXT = /\.(csv|tsv)$/i;
  var JSON_EXT = /\.json$/i;
  var CODE_EXT =
    /\.(py|js|jsx|ts|tsx|css|scss|less|java|go|rb|php|rs|c|h|cpp|hpp|cs|swift|kt|scala|sh|bash|zsh|ps1|sql|r|lua|pl|vue|svelte|xml|toml|ini|ya?ml|diff|patch)$/i;
  var TXT_EXT = /\.(txt|log|env|cfg|conf)$/i;
  var PDF_EXT = /\.pdf$/i;

  var ICO_FILE =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">' +
    '<path d="M3.5 2.5h6l3 3v8a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/>' +
    '<path d="M9.5 2.5v3h3"/></svg>';

  var ICO_REFRESH =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M13.5 8a5.5 5.5 0 1 1-1.4-3.6"/><path d="M13.5 3.5v3h-3"/></svg>';
  var ICO_EXPAND =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5l-4 4M2.5 13.5l4-4"/></svg>';
  var ICO_COMPRESS =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M9.5 6.5h4v-4M6.5 9.5h-4v4M13.5 2.5l-4 4M2.5 13.5l4-4"/></svg>';
  var ICO_EXTERNAL =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M9 3h4v4M13 3 8 8"/><path d="M11 9.5V12a1.5 1.5 0 0 1-1.5 1.5h-6A1.5 1.5 0 0 1 2 12V6A1.5 1.5 0 0 1 3.5 4.5H6"/></svg>';
  var ICO_CLOSE =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">' +
    '<path d="M4 4l8 8M12 4l-8 8"/></svg>';
  var ICO_BACK =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M10 3.5 5.5 8 10 12.5"/></svg>';
  var ICO_SHARE =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<path d="M4 10v2a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V8"/><path d="M8 2v8"/><path d="M5 5l3-3 3 3"/></svg>';

  var HTML_SANDBOX = "allow-scripts allow-forms allow-popups allow-modals allow-downloads";

  var LANG_BY_EXT = {
    py: "python",
    js: "javascript",
    jsx: "javascript",
    ts: "typescript",
    tsx: "typescript",
    md: "markdown",
    markdown: "markdown",
    yml: "yaml",
    yaml: "yaml",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    ps1: "powershell",
    htm: "html",
    html: "html",
    rs: "rust",
    kt: "kotlin",
    cs: "csharp",
    cpp: "cpp",
    hpp: "cpp",
    h: "c",
    rb: "ruby",
    pl: "perl",
  };

  var _state = {
    view: "home", // home | files | preview
    art: null,
    sessionId: "",
    openTabs: [],
    panelOpen: false,
    maximized: false,
    // User closed the side panel (✕). Do not auto-open on later tool results
    // or history replays until they open Files again.
    userCollapsed: false,
    previewMode: "render", // render | source (html/csv/md)
  };

  function category(filename) {
    var name = filename || "";
    if (IMG_EXT.test(name)) return "image";
    if (VID_EXT.test(name)) return "video";
    if (AUD_EXT.test(name)) return "sound";
    if (PDF_EXT.test(name)) return "pdf";
    if (HTML_EXT.test(name)) return "html";
    if (MD_EXT.test(name)) return "markdown";
    if (CSV_EXT.test(name)) return "csv";
    if (JSON_EXT.test(name)) return "json";
    if (CODE_EXT.test(name)) return "code";
    if (TXT_EXT.test(name)) return "text";
    return "data";
  }

  function extOf(filename) {
    var m = String(filename || "").match(/\.([^.]+)$/);
    return m ? m[1].toLowerCase() : "";
  }

  function langFor(filename) {
    var ext = extOf(filename);
    return LANG_BY_EXT[ext] || ext || "plaintext";
  }

  function looksLikeHtml(text) {
    var t = String(text || "")
      .replace(/^\uFEFF/, "")
      .trim()
      .slice(0, 800)
      .toLowerCase();
    if (!t) return false;
    if (t.indexOf("<!doctype html") === 0) return true;
    if (t.indexOf("<html") === 0) return true;
    return (
      /<(html|head|body)\b/.test(t) &&
      /<\/(html|head|body|div|section|main)>/.test(t)
    );
  }

  function looksLikeJson(text) {
    var t = String(text || "")
      .replace(/^\uFEFF/, "")
      .trim();
    if (!t) return false;
    var c0 = t.charAt(0);
    if (c0 !== "{" && c0 !== "[") return false;
    try {
      JSON.parse(t);
      return true;
    } catch (_) {
      return false;
    }
  }

  function htmlFrame(attrs) {
    var extra = attrs.tall ? " ap-iframe-tall" : "";
    var srcAttr = attrs.src ? ' src="' + esc(attrs.src) + '"' : "";
    return (
      '<iframe class="ap-iframe ap-html-frame' +
      extra +
      '"' +
      srcAttr +
      ' title="' +
      esc(attrs.title || "HTML preview") +
      '" sandbox="' +
      HTML_SANDBOX +
      '" referrerpolicy="no-referrer"></iframe>'
    );
  }

  /** Load HTML into a sandboxed iframe via srcdoc (never Tomo-origin src). */
  function fillHtmlFrame(iframe, url, textOpt) {
    if (!iframe) return;
    var nudgeChat = function () {
      var scroll = iframe.closest && iframe.closest(".chat-scroll");
      // Only re-pin while an active stick is still holding — never yank after the user left.
      if (scroll && typeof scroll._tomoStickGo === "function") {
        scroll._tomoStickGo();
      }
    };
    var apply = function (text) {
      iframe.removeAttribute("src");
      iframe.srcdoc = text;
      // srcdoc paint can arrive after history stick — re-pin if still active.
      requestAnimationFrame(nudgeChat);
    };
    if (typeof textOpt === "string") {
      apply(textOpt);
      return;
    }
    if (!url) {
      apply("<pre>No HTML content</pre>");
      return;
    }
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(apply)
      .catch(function (err) {
        apply(
          "<pre style='padding:12px;font:12px monospace'>Could not load HTML: " +
            esc(err && err.message ? err.message : "error") +
            "</pre>"
        );
      });
  }

  /** Minimal CSV/TSV parser → rows of string cells. */
  function parseDelimited(text, delim) {
    var rows = [];
    var row = [];
    var cell = "";
    var i = 0;
    var inQ = false;
    var s = String(text || "").replace(/^\uFEFF/, "");
    while (i < s.length) {
      var ch = s.charAt(i);
      if (inQ) {
        if (ch === '"') {
          if (s.charAt(i + 1) === '"') {
            cell += '"';
            i += 2;
            continue;
          }
          inQ = false;
          i++;
          continue;
        }
        cell += ch;
        i++;
        continue;
      }
      if (ch === '"') {
        inQ = true;
        i++;
        continue;
      }
      if (ch === delim) {
        row.push(cell);
        cell = "";
        i++;
        continue;
      }
      if (ch === "\n" || (ch === "\r" && s.charAt(i + 1) === "\n")) {
        if (ch === "\r") i++;
        row.push(cell);
        rows.push(row);
        row = [];
        cell = "";
        i++;
        continue;
      }
      if (ch === "\r") {
        row.push(cell);
        rows.push(row);
        row = [];
        cell = "";
        i++;
        continue;
      }
      cell += ch;
      i++;
    }
    if (cell.length || row.length) {
      row.push(cell);
      rows.push(row);
    }
    return rows.filter(function (r) {
      return r.length > 1 || (r.length === 1 && r[0] !== "");
    });
  }

  function renderCsvTable(text, filename) {
    var delim = /\.tsv$/i.test(filename) ? "\t" : ",";
    var rows = parseDelimited(text, delim);
    var wrap = document.createElement("div");
    wrap.className = "ap-table-wrap";
    if (!rows.length) {
      wrap.innerHTML = '<div class="cap-msg">Empty table</div>';
      return wrap;
    }
    var maxRows = 500;
    var truncated = rows.length > maxRows;
    var shown = truncated ? rows.slice(0, maxRows) : rows;
    var table = document.createElement("table");
    table.className = "ap-table";
    shown.forEach(function (cells, ri) {
      var tr = document.createElement("tr");
      cells.forEach(function (c) {
        var cell = document.createElement(ri === 0 ? "th" : "td");
        cell.textContent = c;
        tr.appendChild(cell);
      });
      if (ri === 0) {
        var thead = document.createElement("thead");
        thead.appendChild(tr);
        table.appendChild(thead);
      } else {
        var tbody = table.querySelector("tbody");
        if (!tbody) {
          tbody = document.createElement("tbody");
          table.appendChild(tbody);
        }
        tbody.appendChild(tr);
      }
    });
    // single-row file: treat as body not header-only
    if (shown.length === 1 && table.querySelector("thead") && !table.querySelector("tbody")) {
      var only = table.querySelector("thead tr");
      if (only) {
        only.querySelectorAll("th").forEach(function (th) {
          var td = document.createElement("td");
          td.textContent = th.textContent;
          th.replaceWith(td);
        });
        var tb = document.createElement("tbody");
        tb.appendChild(only);
        table.innerHTML = "";
        table.appendChild(tb);
      }
    }
    wrap.appendChild(table);
    if (truncated) {
      var note = document.createElement("div");
      note.className = "cap-msg faint";
      note.textContent = "Showing first " + maxRows + " of " + rows.length + " rows";
      wrap.appendChild(note);
    }
    return wrap;
  }

  function renderHighlightedCode(text, filename, langHint) {
    var lang = langHint || langFor(filename);
    var pre = document.createElement("pre");
    pre.className = "ap-pre ap-code";
    var code = document.createElement("code");
    if (lang) code.className = "language-" + lang;
    code.textContent = text;
    pre.appendChild(code);
    if (typeof hljs !== "undefined" && hljs.highlightElement) {
      try {
        hljs.highlightElement(code);
      } catch (_) {}
    }
    return pre;
  }

  function renderPrettyJson(text) {
    var pretty = text;
    try {
      pretty = JSON.stringify(JSON.parse(text), null, 2);
    } catch (_) {}
    return renderHighlightedCode(pretty, "data.json", "json");
  }

  function fullPageUrl(art) {
    var sid = sessionIdFrom(art, findChatWrap());
    var fn = art && art.filename;
    if (sid && fn) {
      return (
        "/sessions/" +
        encodeURIComponent(sid) +
        "/artifacts/" +
        encodeURIComponent(fn) +
        "/view"
      );
    }
    return (art && art.url) || "#";
  }

  function setMaximized(wrap, on) {
    _state.maximized = !!on;
    wrap = wrap || findChatWrap();
    if (!wrap) return;
    wrap.classList.toggle("is-artifact-max", !!on);
    var panel = wrap.querySelector(".chat-agent-panel");
    if (panel) panel.classList.toggle("is-maximized", !!on);
  }

  function wireMaxToggle(root, wrap) {
    if (!root) return;
    root.querySelectorAll("[data-cap-max-toggle]").forEach(function (btn) {
      if (btn.dataset.maxWired === "1") return;
      btn.dataset.maxWired = "1";
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        setMaximized(wrap, !_state.maximized);
        syncMaxToggleLabels(wrap);
      });
    });
    syncMaxToggleLabels(wrap);
  }

  function syncMaxToggleLabels(wrap) {
    var maximized = !!_state.maximized;
    var scope =
      (wrap && wrap.querySelector(".chat-agent-panel")) ||
      document.querySelector(".chat-agent-panel.is-maximized") ||
      document;
    scope.querySelectorAll("[data-cap-max-toggle]").forEach(function (el) {
      el.title = maximized ? "Restore side panel" : "Open as full page";
      el.setAttribute("aria-label", el.title);
      el.classList.toggle("is-max", maximized);
      // Icon buttons keep their SVG; text buttons get a label.
      if (el.classList.contains("cap-art-btn")) {
        el.innerHTML = maximized ? ICO_COMPRESS : ICO_EXPAND;
        return;
      }
      el.textContent = maximized ? "Minimize" : "Full page";
    });
  }

  function maxToggleHtml() {
    var maximized = !!_state.maximized;
    return (
      '<button type="button" class="ap-html-ext ap-fullpage-btn' +
      (maximized ? " is-max" : "") +
      '" data-cap-max-toggle="1" title="' +
      (maximized ? "Restore side panel" : "Open as full page") +
      '">' +
      (maximized ? "Minimize" : "Full page") +
      "</button>"
    );
  }

  function supportsCodePreview(art) {
    var cat = (art && art.category) || category((art && art.filename) || "");
    return (
      cat === "html" ||
      cat === "markdown" ||
      cat === "csv" ||
      cat === "json" ||
      cat === "code" ||
      cat === "text"
    );
  }

  function modeToolbar(modes, active, artOrUrl) {
    // Legacy/embedded toolbar (kept for rare non-panel hosts). Panel chrome
    // uses the Code | Preview segment in the header instead.
    var art =
      artOrUrl && typeof artOrUrl === "object"
        ? artOrUrl
        : { url: artOrUrl || "" };
    var url = art.url || "";
    var viewUrl = fullPageUrl(art);
    var html =
      '<div class="ap-html-toolbar">' +
      modes
        .map(function (m) {
          var label = m.id === "source" ? "Code" : m.id === "render" ? "Preview" : m.label;
          return (
            '<button type="button" class="ap-html-mode' +
            (active === m.id ? " active" : "") +
            '" data-preview-mode="' +
            esc(m.id) +
            '">' +
            esc(label) +
            "</button>"
          );
        })
        .join("");
    html += maxToggleHtml();
    if (viewUrl && viewUrl !== "#") {
      html +=
        '<a class="ap-html-popout" href="' +
        esc(viewUrl) +
        '" target="_blank" rel="noopener" title="Open in new browser tab">↗</a>';
    } else if (url) {
      html +=
        '<a class="ap-html-popout" href="' +
        esc(url) +
        '" target="_blank" rel="noopener" title="Open raw file">↗</a>';
    }
    html += "</div>";
    return html;
  }

  function formatBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function prettyTitle(filename) {
    var base = String(filename || "file").replace(/\.[^.]+$/, "");
    base = base.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    if (!base) return filename || "file";
    return base.replace(/\b\w/g, function (c) {
      return c.toUpperCase();
    });
  }

  function findChatWrap() {
    var active = document.querySelector(
      ".chat-wrap.sessions-chat:not([style*='display: none']):not([style*='display:none'])"
    );
    if (active && active.offsetParent !== null) return active;
    if (active && active.style && active.style.display === "flex") return active;
    var studio = document.querySelector("#panel-chat .chat-wrap, .agent-studio .chat-wrap");
    if (studio) return studio;
    var shown = document.querySelector(".chat-wrap.sessions-chat");
    if (shown && shown.style.display !== "none") return shown;
    return document.querySelector(".chat-wrap");
  }

  function sessionIdFrom(art, wrap) {
    if (art && art.session_id) return art.session_id;
    if (art && art.url) {
      var m = String(art.url).match(/\/api\/sessions\/([^/]+)\/artifacts\//);
      if (m) return m[1];
    }
    if (wrap && wrap.dataset && wrap.dataset.sessionId) return wrap.dataset.sessionId;
    return "";
  }

  function ensurePanel(wrap) {
    var panel = wrap.querySelector(".chat-agent-panel");
    if (!panel) {
      panel = document.createElement("aside");
      panel.className = "chat-agent-panel";
      panel.setAttribute("aria-label", "Artifacts");
      panel.dataset.capOpen = "0";
      panel.innerHTML =
        '<div class="cap-resize" role="separator" aria-orientation="vertical" aria-label="Resize panel" title="Drag to resize" tabindex="0"></div>' +
        '<button type="button" class="cap-expand-strip" title="Open artifacts" aria-label="Open artifacts panel">' +
        '<span class="cap-expand-ico" aria-hidden="true">' +
        ICO_FILE +
        "</span>" +
        '<span class="cap-expand-label">Artifacts</span>' +
        "</button>" +
        '<div class="cap-body" data-cap-root></div>';
      wrap.appendChild(panel);
    }
    wirePanelResize(panel);
    applyStoredPanelWidth(panel);
    return panel;
  }

  var CAP_WIDTH_KEY = "tomo.agentPanelWidth.v5";
  var CAP_HEIGHT_KEY = "tomo.agentPanelHeight";
  var CAP_MIN_W = 380;
  var CAP_MAX_W = 1200;
  var CAP_DEFAULT_W = 640;
  var CAP_CHAT_MIN = 340;
  var CAP_GAP = 24;
  var CAP_MIN_H = 180;
  var CAP_DEFAULT_H = 360;

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  function wrapInnerWidth(panel) {
    var wrap = panel && panel.closest ? panel.closest(".chat-wrap") : null;
    return (wrap && wrap.clientWidth) || window.innerWidth || 1100;
  }

  function maxPanelWidthPx(panel) {
    return Math.max(
      CAP_MIN_W,
      Math.min(CAP_MAX_W, wrapInnerWidth(panel) - CAP_CHAT_MIN - CAP_GAP)
    );
  }

  function defaultPanelWidthPx(panel) {
    var w = wrapInnerWidth(panel);
    // Gemini-like: canvas ~55% of the chat shell, chat keeps ≥340px.
    return clamp(Math.round(w * 0.55), CAP_MIN_W, maxPanelWidthPx(panel));
  }

  function syncCapWidthVar(panel, px) {
    if (!panel) return;
    var capped = clamp(px, CAP_MIN_W, maxPanelWidthPx(panel));
    var val = Math.round(capped) + "px";
    panel.style.setProperty("--cap-width", val);
    var wrap = panel.closest(".chat-wrap");
    if (wrap) wrap.style.setProperty("--cap-width", val);
    return capped;
  }

  function applyStoredPanelWidth(panel) {
    if (!panel) return;
    var stored = 0;
    try {
      stored = parseInt(localStorage.getItem(CAP_WIDTH_KEY) || "", 10);
    } catch (_) {}
    var w = stored >= CAP_MIN_W ? stored : defaultPanelWidthPx(panel);
    w = syncCapWidthVar(panel, w);
    savePanelWidth(w);

    var hStored = 0;
    try {
      hStored = parseInt(localStorage.getItem(CAP_HEIGHT_KEY) || "", 10);
    } catch (_) {}
    if (hStored >= CAP_MIN_H) {
      panel.style.setProperty("--cap-height", hStored + "px");
    }
  }

  function savePanelWidth(px) {
    try {
      localStorage.setItem(CAP_WIDTH_KEY, String(Math.round(px)));
    } catch (_) {}
  }

  function savePanelHeight(px) {
    try {
      localStorage.setItem(CAP_HEIGHT_KEY, String(Math.round(px)));
    } catch (_) {}
  }

  function isPanelStacked() {
    // Floating canvas is never a bottom sheet — always width-resize.
    return false;
  }

  function wirePanelResize(panel) {
    if (!panel || panel.dataset.capResizeWired === "1") return;
    panel.dataset.capResizeWired = "1";
    var handle = panel.querySelector(".cap-resize");
    if (!handle) return;

    var drag = null;

    function onMove(e) {
      if (!drag) return;
      var pt = e.touches && e.touches[0] ? e.touches[0] : e;
      if (drag.mode === "col") {
        var wrap = panel.closest(".chat-wrap");
        var panelRight = panel.getBoundingClientRect().right;
        var maxW = maxPanelWidthPx(panel);
        var next = clamp(panelRight - pt.clientX, CAP_MIN_W, maxW);
        syncCapWidthVar(panel, next);
        drag.last = next;
      } else {
        var wrapEl = panel.closest(".chat-wrap");
        var wrapBottom = wrapEl ? wrapEl.getBoundingClientRect().bottom : window.innerHeight;
        var maxH = Math.min(Math.floor(window.innerHeight * 0.7), wrapEl ? wrapEl.clientHeight - 120 : 600);
        var nextH = clamp(wrapBottom - pt.clientY, CAP_MIN_H, maxH);
        panel.style.setProperty("--cap-height", nextH + "px");
        drag.last = nextH;
      }
      if (e.cancelable) e.preventDefault();
    }

    function onUp() {
      if (!drag) return;
      panel.classList.remove("is-resizing");
      document.body.classList.remove("cap-resizing", "cap-resizing-row");
      if (drag.mode === "col") savePanelWidth(drag.last || CAP_DEFAULT_W);
      else savePanelHeight(drag.last || CAP_DEFAULT_H);
      drag = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
    }

    function startDrag(e, mode) {
      if (panel.dataset.capOpen !== "1") return;
      var pt = e.touches && e.touches[0] ? e.touches[0] : e;
      drag = {
        mode: mode || (isPanelStacked(panel) ? "row" : "col"),
        last:
          mode === "row" || (!mode && isPanelStacked(panel))
            ? panel.getBoundingClientRect().height
            : panel.getBoundingClientRect().width,
      };
      panel.classList.add("is-resizing");
      document.body.classList.add("cap-resizing");
      document.body.classList.toggle("cap-resizing-row", drag.mode === "row");
      if (pt.pointerId != null && handle.setPointerCapture) {
        try {
          handle.setPointerCapture(pt.pointerId);
        } catch (_) {}
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onUp);
      if (e.cancelable) e.preventDefault();
    }

    handle.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      startDrag(e);
    });
    handle.addEventListener("touchstart", function (e) {
      startDrag(e);
    }, { passive: false });

    // Double-click / dbl-tap resets to default size
    handle.addEventListener("dblclick", function () {
      if (isPanelStacked(panel)) {
        panel.style.setProperty("--cap-height", CAP_DEFAULT_H + "px");
        savePanelHeight(CAP_DEFAULT_H);
      } else {
        var dw = defaultPanelWidthPx(panel);
        syncCapWidthVar(panel, dw);
        savePanelWidth(dw);
      }
    });

    // Keyboard: ←/→ (or ↑/↓ when stacked)
    handle.addEventListener("keydown", function (e) {
      if (panel.dataset.capOpen !== "1") return;
      var stacked = isPanelStacked(panel);
      var step = e.shiftKey ? 40 : 16;
      if (!stacked && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        var cur = panel.getBoundingClientRect().width;
        var next = clamp(
          cur + (e.key === "ArrowLeft" ? step : -step),
          CAP_MIN_W,
          maxPanelWidthPx(panel)
        );
        syncCapWidthVar(panel, next);
        savePanelWidth(next);
        e.preventDefault();
      } else if (stacked && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        var curH = panel.getBoundingClientRect().height;
        var nextH = clamp(curH + (e.key === "ArrowUp" ? step : -step), CAP_MIN_H, 700);
        panel.style.setProperty("--cap-height", nextH + "px");
        savePanelHeight(nextH);
        e.preventDefault();
      } else if (e.key === "Home") {
        if (stacked) {
          panel.style.setProperty("--cap-height", CAP_DEFAULT_H + "px");
          savePanelHeight(CAP_DEFAULT_H);
        } else {
          var homeW = defaultPanelWidthPx(panel);
          syncCapWidthVar(panel, homeW);
          savePanelWidth(homeW);
        }
        e.preventDefault();
      }
    });
  }

  function rememberTab(art) {
    if (!art || !art.url) return;
    var key = art.url;
    _state.openTabs = _state.openTabs.filter(function (t) {
      return t.url !== key;
    });
    _state.openTabs.unshift({
      url: art.url,
      filename: art.filename || "file",
      title: art.title || prettyTitle(art.filename || "file"),
      category: art.category || category(art.filename || ""),
      size: art.size,
      session_id: art.session_id,
    });
    if (_state.openTabs.length > 12) _state.openTabs.length = 12;
  }

  function setPanelOpen(wrap, on) {
    _state.panelOpen = !!on;
    var panel = wrap && wrap.querySelector(".chat-agent-panel");
    if (panel) {
      panel.dataset.capOpen = on ? "1" : "0";
      if (on) {
        wirePanelResize(panel);
        applyStoredPanelWidth(panel);
      } else {
        setMaximized(wrap, false);
        if (wrap) wrap.style.removeProperty("--cap-width");
      }
    }
  }

  /** Inline artifact chip in the chat turn (opens side canvas). */
  function buildSavedCard(art) {
    var filename = art.filename || "file";
    var url = art.url || "";
    var size = art.size;
    var cat = art.category || category(filename);
    var title = prettyTitle(filename);
    var kindLabel =
      cat === "html"
        ? "Canvas · HTML"
        : cat === "markdown"
          ? "Canvas · Markdown"
          : cat === "image"
            ? "Image"
            : cat === "pdf"
              ? "PDF"
              : cat === "csv"
                ? "Table · CSV"
                : cat === "code" || cat === "json"
                  ? "Code"
                  : "File";

    var el = document.createElement("button");
    el.type = "button";
    el.className = "artifact-chip artifact-inline-open";
    el.dataset.filename = filename;
    el.dataset.url = url;
    el.dataset.category = cat;
    el.setAttribute("aria-label", "Open " + title);

    var thumb = "";
    if (cat === "image" && url) {
      thumb =
        '<span class="artifact-chip-thumb"><img src="' +
        esc(url) +
        '" alt="" loading="lazy"></span>';
    } else {
      thumb = '<span class="artifact-chip-ico" aria-hidden="true">' + ICO_FILE + "</span>";
    }

    el.innerHTML =
      thumb +
      '<span class="artifact-chip-meta">' +
      '<span class="artifact-chip-title">' +
      esc(title) +
      "</span>" +
      '<span class="artifact-chip-kind">' +
      esc(kindLabel) +
      (size != null ? " · " + formatBytes(size) : "") +
      "</span>" +
      "</span>" +
      '<span class="artifact-chip-go" aria-hidden="true">↗</span>';

    el.addEventListener("click", function (e) {
      e.preventDefault();
      openPreview(
        {
          url: url,
          filename: filename,
          category: cat,
          session_id: art.session_id,
          size: size,
        },
        { userGesture: true }
      );
    });
    return el;
  }

  function fillInlineRich(host, cat, url, filename) {
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (text) {
        host.innerHTML = "";
        if (cat === "markdown" && global.TomoMarkdown && global.TomoMarkdown.renderInto) {
          var box = document.createElement("div");
          box.className = "ap-md chat-prose prose artifact-inline-md";
          host.appendChild(box);
          global.TomoMarkdown.renderInto(box, text);
          return;
        }
        if (cat === "csv") {
          host.appendChild(renderCsvTable(text, filename));
          return;
        }
        if (cat === "json") {
          host.appendChild(renderPrettyJson(text));
          return;
        }
        if (cat === "code") {
          host.appendChild(renderHighlightedCode(text, filename));
          return;
        }
        var pre = document.createElement("pre");
        pre.className = "ap-pre";
        pre.textContent = text.slice(0, 4000);
        host.appendChild(pre);
      })
      .catch(function () {
        host.innerHTML =
          '<div class="artifact-inline-file"><button type="button" class="btn sm artifact-inline-open">Open in panel</button></div>';
      });
  }

  function closePreview() {
    _state.view = "home";
    _state.art = null;
    var wrap = findChatWrap();
    if (wrap) {
      setPanelOpen(wrap, true);
      renderPanel(wrap);
    }
  }

  function closePanel() {
    _state.view = "home";
    _state.art = null;
    _state.panelOpen = false;
    _state.userCollapsed = true;
    var wrap = findChatWrap();
    if (wrap) {
      setMaximized(wrap, false);
      wrap.style.removeProperty("--cap-width");
    }
    document.querySelectorAll(".chat-agent-panel").forEach(function (p) {
      p.dataset.capOpen = "0";
      p.classList.remove("is-maximized");
    });
    document.querySelectorAll(".chat-wrap.is-artifact-max").forEach(function (w) {
      w.classList.remove("is-artifact-max");
      w.style.removeProperty("--cap-width");
    });
  }

  function renderHtmlPreview(body, art, textOpt) {
    var url = art.url || "";
    var filename = art.filename || "file";
    var mode = _state.previewMode === "source" ? "source" : "render";

    body.innerHTML = '<div class="ap-html-stage"></div>';
    var stage = body.querySelector(".ap-html-stage");

    if (mode === "source") {
      var showSource = function (text) {
        stage.innerHTML = "";
        stage.appendChild(renderHighlightedCode(text, filename, "html"));
      };
      if (typeof textOpt === "string") showSource(textOpt);
      else {
        stage.innerHTML = '<div class="cap-msg faint">Loading…</div>';
        fetch(url, { credentials: "same-origin" })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.text();
          })
          .then(showSource)
          .catch(function (err) {
            stage.innerHTML =
              '<div class="cap-msg">Could not load source: ' +
              esc(err && err.message ? err.message : "error") +
              "</div>";
          });
      }
    } else {
      stage.innerHTML = htmlFrame({
        title: filename,
        tall: true,
      });
      var iframe = stage.querySelector("iframe");
      fillHtmlFrame(iframe, url, typeof textOpt === "string" ? textOpt : undefined);
    }
  }

  function renderTextualPreview(body, art, text) {
    var filename = art.filename || "file";
    var cat = art.category || category(filename);
    var mode = _state.previewMode === "source" ? "source" : "render";

    // Resolve effective kind (sniff when needed)
    var kind = cat;
    if (kind === "text" || kind === "data" || kind === "document") {
      if (looksLikeHtml(text)) kind = "html";
      else if (looksLikeJson(text)) kind = "json";
      else if (MD_EXT.test(filename)) kind = "markdown";
      else if (CSV_EXT.test(filename)) kind = "csv";
      else if (CODE_EXT.test(filename)) kind = "code";
    }

    if (kind === "html") {
      art.category = "html";
      renderHtmlPreview(body, art, text);
      return;
    }

    var hasRender =
      kind === "markdown" || kind === "csv" || kind === "json" || kind === "code";
    if (!hasRender) mode = "source";

    body.innerHTML = '<div class="ap-html-stage"></div>';
    var stage = body.querySelector(".ap-html-stage");

    if (mode === "render" && kind === "markdown") {
      if (global.TomoMarkdown && global.TomoMarkdown.renderInto) {
        var box = document.createElement("div");
        box.className = "ap-md chat-prose prose";
        stage.appendChild(box);
        global.TomoMarkdown.renderInto(box, text);
      } else {
        stage.appendChild(renderHighlightedCode(text, filename, "markdown"));
      }
    } else if (mode === "render" && kind === "csv") {
      stage.appendChild(renderCsvTable(text, filename));
    } else if (mode === "render" && kind === "json") {
      stage.appendChild(renderPrettyJson(text));
    } else if (mode === "render" && kind === "code") {
      stage.appendChild(renderHighlightedCode(text, filename));
    } else if (kind === "json") {
      stage.appendChild(renderPrettyJson(text));
    } else if (kind === "code" || kind === "markdown" || kind === "csv") {
      stage.appendChild(renderHighlightedCode(text, filename, langFor(filename)));
    } else {
      var pre = document.createElement("pre");
      pre.className = "ap-pre";
      pre.textContent = text;
      stage.appendChild(pre);
    }
  }

  function renderPreviewInto(body, art) {
    var url = art.url || "";
    var filename = art.filename || "file";
    var cat = art.category || category(filename);

    if (!url) {
      body.innerHTML = '<div class="cap-msg">Pick a file to preview.</div>';
      return;
    }
    body.innerHTML = '<div class="cap-msg faint">Loading…</div>';

    if (cat === "image") {
      body.innerHTML =
        '<div class="ap-media"><img class="ap-img" src="' +
        esc(url) +
        '" alt="' +
        esc(filename) +
        '"></div>';
      return;
    }
    if (cat === "video") {
      body.innerHTML =
        '<div class="ap-media"><video class="ap-video" src="' +
        esc(url) +
        '" controls></video></div>';
      return;
    }
    if (cat === "sound") {
      body.innerHTML =
        '<div class="ap-audio"><audio src="' + esc(url) + '" controls></audio></div>';
      return;
    }
    if (cat === "pdf") {
      body.innerHTML =
        '<iframe class="ap-iframe ap-iframe-tall" src="' +
        esc(url) +
        '" title="' +
        esc(filename) +
        '"></iframe>';
      return;
    }
    if (cat === "html") {
      renderHtmlPreview(body, art);
      return;
    }

    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        var ct = (r.headers.get("content-type") || "").toLowerCase();
        // Binary / unknown non-text → download affordance
        if (
          cat === "data" &&
          ct &&
          !/^text\//.test(ct) &&
          ct.indexOf("json") < 0 &&
          ct.indexOf("xml") < 0 &&
          ct.indexOf("javascript") < 0
        ) {
          throw new Error("binary");
        }
        return r.text();
      })
      .then(function (text) {
        renderTextualPreview(body, art, text);
      })
      .catch(function (err) {
        var msg = err && err.message === "binary" ? "Binary file — open or download instead." : null;
        body.innerHTML =
          '<div class="cap-msg">' +
          esc(msg || (err && err.message ? err.message : "Could not preview")) +
          ' — <a href="' +
          esc(url) +
          '" target="_blank" rel="noopener">open</a></div>';
      });
  }

  function renderFilesList(body, sid, current) {
    if (!sid) {
      body.innerHTML = '<div class="cap-msg">No session yet.</div>';
      return;
    }
    body.innerHTML = '<div class="cap-msg faint">Loading files…</div>';
    var api =
      typeof Tomo !== "undefined" && Tomo.api
        ? Tomo.api("/api/sessions/" + encodeURIComponent(sid) + "/artifacts?limit=100&sort=newest")
        : fetch("/api/sessions/" + encodeURIComponent(sid) + "/artifacts?limit=100&sort=newest", {
            credentials: "same-origin",
          }).then(function (r) {
            return r.json();
          });

    Promise.resolve(api)
      .then(function (data) {
        var files = (data && data.files) || [];
        if (!files.length) {
          body.innerHTML =
            '<div class="cap-msg">No files in this session yet.<br><span class="faint">Artifacts appear here after <code>save_artifact</code>.</span></div>';
          return;
        }
        var list = document.createElement("div");
        list.className = "cap-file-list";
        files.forEach(function (f) {
          var row = document.createElement("button");
          row.type = "button";
          row.className =
            "cap-row cap-file-row" +
            (current && current.filename === f.filename ? " active" : "");
          row.innerHTML =
            '<span class="cap-ico">' +
            ICO_FILE +
            '</span><span class="cap-row-text"><span class="cap-label">' +
            esc(prettyTitle(f.filename)) +
            '</span><span class="cap-meta">' +
            esc(f.filename) +
            " · " +
            formatBytes(f.size) +
            "</span></span>";
          row.addEventListener("click", function () {
            openPreview(
              {
                url: f.url,
                filename: f.filename,
                category: f.category,
                size: f.size,
                session_id: sid,
              },
              { userGesture: true }
            );
          });
          list.appendChild(row);
        });
        body.innerHTML = "";
        body.appendChild(list);
      })
      .catch(function (err) {
        body.innerHTML =
          '<div class="cap-msg">Could not list files: ' +
          esc(err && err.message ? err.message : "error") +
          "</div>";
      });
  }

  function renderHome(root, wrap) {
    var tabsHtml;
    if (!_state.openTabs.length) {
      tabsHtml = '<div class="cap-empty-hint">No open files</div>';
    } else {
      tabsHtml = _state.openTabs
        .map(function (t) {
          var active =
            _state.art && _state.art.url === t.url && _state.view === "preview" ? " active" : "";
          return (
            '<button type="button" class="cap-row' +
            active +
            '" data-cap-tab="' +
            esc(t.url) +
            '">' +
            '<span class="cap-ico">' +
            ICO_FILE +
            '</span><span class="cap-label">' +
            esc(t.title || prettyTitle(t.filename)) +
            "</span></button>"
          );
        })
        .join("");
    }

    root.innerHTML =
      '<div class="cap-home">' +
      '<div class="cap-panel-head cap-panel-head-quiet">' +
      '<span class="cap-panel-title"></span>' +
      '<button type="button" class="cap-icon-btn cap-collapse" title="Collapse panel" aria-label="Collapse">✕</button>' +
      "</div>" +
      '<section class="cap-section">' +
      '<h3 class="cap-section-title">Open Tabs</h3>' +
      '<div class="cap-rows">' +
      tabsHtml +
      "</div>" +
      "</section>" +
      '<section class="cap-section">' +
      '<h3 class="cap-section-title">Library</h3>' +
      '<div class="cap-rows">' +
      '<button type="button" class="cap-row' +
      (_state.view === "files" ? " active" : "") +
      '" data-cap-nav="files">' +
      '<span class="cap-ico">' +
      ICO_FILE +
      '</span><span class="cap-label">All files</span>' +
      "</button>" +
      "</div>" +
      "</section>" +
      "</div>";

    root.querySelector(".cap-collapse").addEventListener("click", function () {
      closePanel();
    });
    root.querySelectorAll("[data-cap-nav='files']").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        openFilesPane({ wrap: wrap, toggle: false });
      });
    });
    root.querySelectorAll("[data-cap-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var url = btn.getAttribute("data-cap-tab");
        var tab = _state.openTabs.find(function (t) {
          return t.url === url;
        });
        if (tab) openPreview(tab);
      });
    });
  }

  function renderDrill(root, wrap, kind) {
    var title =
      kind === "files"
        ? "Files"
        : (_state.art && (_state.art.title || prettyTitle(_state.art.filename))) || "Preview";
    var art = _state.art || {};
    var mode = _state.previewMode === "source" ? "source" : "render";
    var showSeg = kind === "preview" && supportsCodePreview(art);
    var viewUrl = kind === "preview" ? fullPageUrl(art) : "";
    var maximized = !!_state.maximized;

    var segHtml = "";
    if (showSeg) {
      segHtml =
        '<div class="cap-art-seg" role="tablist" aria-label="View mode">' +
        '<button type="button" role="tab" class="cap-seg-btn' +
        (mode === "source" ? " active" : "") +
        '" data-preview-mode="source" aria-selected="' +
        (mode === "source" ? "true" : "false") +
        '">Code</button>' +
        '<button type="button" role="tab" class="cap-seg-btn' +
        (mode === "render" ? " active" : "") +
        '" data-preview-mode="render" aria-selected="' +
        (mode === "render" ? "true" : "false") +
        '">Preview</button>' +
        "</div>";
    }

    var actionsHtml = "";
    if (kind === "preview") {
      actionsHtml =
        '<div class="cap-art-actions">' +
        '<button type="button" class="cap-art-btn" data-cap-refresh="1" title="Refresh" aria-label="Refresh">' +
        ICO_REFRESH +
        "</button>" +
        '<button type="button" class="cap-art-btn' +
        (maximized ? " is-max" : "") +
        '" data-cap-max-toggle="1" title="' +
        (maximized ? "Restore side panel" : "Open as full page") +
        '">' +
        (maximized ? ICO_COMPRESS : ICO_EXPAND) +
        "</button>" +
        (viewUrl && viewUrl !== "#"
          ? '<a class="cap-art-btn" href="' +
            esc(viewUrl) +
            '" target="_blank" rel="noopener" title="Open in new tab" aria-label="Open in new tab">' +
            ICO_EXTERNAL +
            "</a>"
          : "") +
        '<button type="button" class="cap-art-btn" data-cap-share="1" title="Share link" aria-label="Share link">' +
        ICO_SHARE +
        "</button>" +
        '<button type="button" class="cap-art-btn cap-collapse" title="Close" aria-label="Close">' +
        ICO_CLOSE +
        "</button>" +
        "</div>";
    } else {
      actionsHtml =
        '<button type="button" class="cap-icon-btn cap-collapse" title="Collapse panel" aria-label="Collapse">' +
        ICO_CLOSE +
        "</button>";
    }

    root.innerHTML =
      '<div class="cap-drill' +
      (kind === "preview" ? " cap-artifact" : "") +
      '">' +
      (kind === "preview"
        ? '<div class="cap-art-chrome">' +
          '<div class="cap-art-lead">' +
          '<button type="button" class="cap-art-btn cap-back" title="Back" aria-label="Back">' +
          ICO_BACK +
          "</button>" +
          '<span class="cap-art-title" title="' +
          esc(title) +
          '">' +
          esc(title) +
          "</span>" +
          "</div>" +
          '<div class="cap-art-trail">' +
          segHtml +
          actionsHtml +
          "</div>" +
          "</div>"
        : '<div class="cap-panel-head">' +
          '<button type="button" class="cap-icon-btn cap-back" title="Back" aria-label="Back">‹</button>' +
          '<span class="cap-panel-title">' +
          esc(title) +
          "</span>" +
          actionsHtml +
          "</div>") +
      '<div class="cap-drill-body"></div>' +
      "</div>";

    root.querySelector(".cap-back").addEventListener("click", function () {
      setMaximized(wrap, false);
      _state.view = "home";
      renderPanel(wrap);
    });
    root.querySelectorAll(".cap-collapse").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setMaximized(wrap, false);
        closePanel();
      });
    });
    wireMaxToggle(root, wrap);

    var shareBtn = root.querySelector("[data-cap-share]");
    if (shareBtn) {
      shareBtn.addEventListener("click", function () {
        var art = _state.art || {};
        var sid = _state.sessionId || art.session_id;
        var fn = art.filename;
        if (!sid || !fn) return;
        if (shareBtn.disabled) return;
        shareBtn.disabled = true;
        var orig = shareBtn.innerHTML;
        shareBtn.innerHTML =
          '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/><path d="M8 4v4l2.5 1.5"/></svg>';
        fetch(
          "/api/sessions/" +
            encodeURIComponent(sid) +
            "/artifacts/" +
            encodeURIComponent(fn) +
            "/share",
          { method: "POST", credentials: "same-origin" }
        )
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (data) {
            var url = window.location.origin + data.share_url;
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(url).catch(function () {});
            }
            window.prompt("Copy share link", url);
          })
          .catch(function () {
            window.alert("Could not create share link.");
          })
          .finally(function () {
            shareBtn.disabled = false;
            shareBtn.innerHTML = orig;
          });
      });
    }

    var body = root.querySelector(".cap-drill-body");
    if (kind === "files") {
      renderFilesList(body, _state.sessionId, _state.art);
      return;
    }

    function fillStage() {
      renderPreviewInto(body, _state.art || {});
    }
    fillStage();

    root.querySelectorAll("[data-preview-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var next = btn.getAttribute("data-preview-mode") || "render";
        if (_state.previewMode === next) return;
        _state.previewMode = next;
        root.querySelectorAll("[data-preview-mode]").forEach(function (b) {
          var on = b.getAttribute("data-preview-mode") === next;
          b.classList.toggle("active", on);
          b.setAttribute("aria-selected", on ? "true" : "false");
        });
        fillStage();
      });
    });
    var refreshBtn = root.querySelector("[data-cap-refresh]");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        fillStage();
      });
    }
  }

  function renderPanel(wrap) {
    if (!wrap) return;
    var panel = ensurePanel(wrap);
    var root = panel.querySelector("[data-cap-root]") || panel;
    setPanelOpen(wrap, true);

    // Remove legacy preview pane if present
    wrap.querySelectorAll(".artifact-preview-pane").forEach(function (p) {
      p.remove();
    });

    if (_state.view === "files") {
      renderDrill(root, wrap, "files");
    } else if (_state.view === "preview" && _state.art && _state.art.url) {
      renderDrill(root, wrap, "preview");
    } else {
      _state.view = "home";
      renderHome(root, wrap);
    }
  }

  function openFilesPane(opts) {
    opts = opts || {};
    var wrap = opts.wrap || findChatWrap();
    var sid =
      opts.session_id ||
      (wrap && wrap.dataset && wrap.dataset.sessionId) ||
      _state.sessionId ||
      "";
    if (opts.userGesture !== false) {
      _state.userCollapsed = false;
    }
    setPanelOpen(wrap, true);
    if (!sid) {
      if (typeof Tomo !== "undefined" && Tomo.toast) {
        Tomo.toast("No active session yet", "err");
      }
      return;
    }
    // Toggle off if already on Files
    if (
      _state.panelOpen &&
      _state.view === "files" &&
      _state.sessionId === sid &&
      opts.toggle !== false
    ) {
      // From composer toggle → go home instead of fully collapsing
      if (opts.fromComposer) {
        _state.view = "home";
        if (wrap) renderPanel(wrap);
        return;
      }
    }
    _state.sessionId = sid;
    _state.view = "files";
    if (!wrap) return;
    renderPanel(wrap);
  }

  function openPreview(art, opts) {
    opts = opts || {};
    if (!art || !art.url) {
      if (art && art.session_id) {
        openFilesPane({ session_id: art.session_id, toggle: false });
      }
      return;
    }
    if (opts.userGesture) _state.userCollapsed = false;
    var wrap = findChatWrap();
    if (!wrap) {
      window.open(art.url, "_blank", "noopener");
      return;
    }
    _state.art = {
      url: art.url,
      filename: art.filename || "file",
      title: art.title || prettyTitle(art.filename || "file"),
      category: art.category || category(art.filename || ""),
      size: art.size,
      session_id: art.session_id,
    };
    _state.sessionId = sessionIdFrom(_state.art, wrap);
    rememberTab(_state.art);
    _state.view = "preview";
    _state.previewMode = "render";
    setPanelOpen(wrap, true);
    renderPanel(wrap);
  }

  function openHome(opts) {
    opts = opts || {};
    var wrap = opts.wrap || findChatWrap();
    if (!wrap) return;
    if (opts.userGesture !== false) _state.userCollapsed = false;
    var sid =
      opts.session_id ||
      (wrap.dataset && wrap.dataset.sessionId) ||
      _state.sessionId ||
      "";
    if (sid) _state.sessionId = sid;
    _state.view = "home";
    setPanelOpen(wrap, true);
    renderPanel(wrap);
  }

  function parseSaveResult(toolName, resultText) {
    var name = (toolName || "").toString();
    var text = typeof resultText === "string" ? resultText : "";
    // Only real save_artifact results — not other tools that happen to return JSON
    // with a filename field (those used to false-trigger auto-open).
    if (name !== "save_artifact") return null;
    if (text.indexOf("Error") === 0) return null;
    try {
      var parsed = JSON.parse(text);
      if (parsed && parsed.filename && parsed.url) {
        parsed.category = parsed.category || category(parsed.filename);
        parsed.title = prettyTitle(parsed.filename);
        return parsed;
      }
    } catch (_) {}
    return null;
  }

  function maybeAutoOpen(art) {
    if (!art || !art.url) return;
    // History replay and post-collapse turns must not yank the panel open.
    if (_state.userCollapsed) return;
    try {
      openPreview(art);
    } catch (_) {}
  }

  function onFilesClick(e) {
    var btn = e.target && e.target.closest ? e.target.closest(".chat-files-btn") : null;
    if (!btn) return;
    e.preventDefault();
    var wrap = btn.closest(".chat-wrap") || findChatWrap();
    var sid =
      (wrap && wrap.dataset && wrap.dataset.sessionId) ||
      (btn.closest("[data-session-id]") &&
        btn.closest("[data-session-id]").dataset.sessionId) ||
      "";

    if (!_state.panelOpen) {
      if (!sid) {
        if (typeof Tomo !== "undefined" && Tomo.toast) Tomo.toast("No active session yet", "err");
        return;
      }
      _state.sessionId = sid;
      openFilesPane({ session_id: sid, wrap: wrap, toggle: false });
      return;
    }
    openFilesPane({ session_id: sid, wrap: wrap, fromComposer: true });
  }

  document.addEventListener("click", onFilesClick);

  // Escape restores the side panel from full-page mode.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || !_state.maximized) return;
    var wrap = findChatWrap();
    if (!wrap) return;
    setMaximized(wrap, false);
    syncMaxToggleLabels(wrap);
  });

  // Collapsed strip → expand to home (Open Tabs + Files)
  document.addEventListener("click", function (e) {
    var strip = e.target && e.target.closest ? e.target.closest(".cap-expand-strip") : null;
    if (!strip) return;
    e.preventDefault();
    var wrap = strip.closest(".chat-wrap") || findChatWrap();
    var sid = (wrap && wrap.dataset && wrap.dataset.sessionId) || "";
    if (sid) _state.sessionId = sid;
    openHome({ wrap: wrap, session_id: sid });
  });

  function bootPanels() {
    document.querySelectorAll(".chat-agent-panel").forEach(function (p) {
      wirePanelResize(p);
      applyStoredPanelWidth(p);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootPanels);
  } else {
    bootPanels();
  }

  function renderFullPage(host, art) {
    if (!host || !art) return;
    host.classList.add("av-rich");
    renderPreviewInto(host, art);
  }

  global.TomoArtifacts = {
    category: category,
    formatBytes: formatBytes,
    buildSavedCard: buildSavedCard,
    openPreview: openPreview,
    openFilesPane: openFilesPane,
    openHome: openHome,
    closePreview: closePreview,
    closePanel: closePanel,
    parseSaveResult: parseSaveResult,
    maybeAutoOpen: maybeAutoOpen,
    renderFullPage: renderFullPage,
    fullPageUrl: fullPageUrl,
  };
})(typeof window !== "undefined" ? window : this);

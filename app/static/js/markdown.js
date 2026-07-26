/* markdown.js — chat markdown pipeline: marked → sanitize → KaTeX → tables → hljs.
 *
 * Ported from the reference chat UI (sanitize + math + code highlight + prose).
 * Exposes window.TomoMarkdown.
 */
(function (global) {
  "use strict";

  var ALLOWED_TAGS = {
    a: 1, b: 1, i: 1, em: 1, strong: 1, code: 1, pre: 1, blockquote: 1,
    ul: 1, ol: 1, li: 1, p: 1, br: 1, hr: 1,
    h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1,
    table: 1, thead: 1, tbody: 1, tr: 1, th: 1, td: 1,
    span: 1, div: 1, img: 1,
  };
  var ALLOWED_ATTRS = {
    a: ["href", "title", "target", "rel"],
    code: ["class"],
    pre: ["class"],
    span: ["class", "style", "aria-hidden"],
    div: ["class"],
    img: ["src", "alt", "class", "loading"],
    ol: ["start"],
  };

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function walkSanitize(node) {
    var children = Array.prototype.slice.call(node.childNodes);
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      if (child.nodeType !== 1) continue;
      var tag = child.tagName.toLowerCase();
      if (!ALLOWED_TAGS[tag]) {
        while (child.firstChild) node.insertBefore(child.firstChild, child);
        node.removeChild(child);
        continue;
      }
      var allowed = ALLOWED_ATTRS[tag] || [];
      var attrs = Array.prototype.slice.call(child.attributes);
      for (var j = 0; j < attrs.length; j++) {
        if (allowed.indexOf(attrs[j].name) === -1) {
          child.removeAttribute(attrs[j].name);
        }
      }
      if (tag === "a") {
        var href = child.getAttribute("href") || "";
        if (/^javascript:/i.test(href) || /^data:/i.test(href)) {
          child.setAttribute("href", "#");
        }
        child.setAttribute("rel", "noopener noreferrer");
        if (/^https?:\/\//i.test(href)) {
          child.setAttribute("target", "_blank");
        }
      }
      if (tag === "img") {
        var src = child.getAttribute("src") || "";
        if (!/^(https?:\/\/|\/|data:image\/)/i.test(src)) {
          child.removeAttribute("src");
        }
      }
      walkSanitize(child);
    }
  }

  function sanitize(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html || "";
    walkSanitize(tpl.content);
    return tpl.innerHTML;
  }

  function renderMath(html) {
    if (typeof katex === "undefined" || !html) return html || "";
    try {
      var codeBlocks = [];
      var safe = String(html).replace(/<pre\b[^>]*>[\s\S]*?<\/pre>/gi, function (m) {
        codeBlocks.push(m);
        return "\0CODE" + (codeBlocks.length - 1) + "\0";
      });
      safe = safe.replace(/<code\b[^>]*>[\s\S]*?<\/code>/gi, function (m) {
        codeBlocks.push(m);
        return "\0CODE" + (codeBlocks.length - 1) + "\0";
      });
      safe = safe.replace(/\$\$([\s\S]*?)\$\$/g, function (m, f) {
        try {
          return katex.renderToString(f, { displayMode: true, throwOnError: false });
        } catch (e) {
          return m;
        }
      });
      safe = safe.replace(/(?<!\$)\$(?!\$)([\s\S]*?)(?<!\$)\$(?!\$)/g, function (m, f) {
        try {
          return katex.renderToString(f, { displayMode: false, throwOnError: false });
        } catch (e) {
          return m;
        }
      });
      safe = safe.replace(/\0CODE(\d+)\0/g, function (m, idx) {
        return codeBlocks[parseInt(idx, 10)] || m;
      });
      return safe;
    } catch (e) {
      return html;
    }
  }

  function wrapTables(html) {
    return String(html || "")
      .replace(/<table/g, '<div class="table-wrapper"><table')
      .replace(/<\/table>/g, "</table></div>");
  }

  function normalizeQuotes(text) {
    return String(text || "").replace(/[\u201c\u201d\u00ab\u00bb]/g, '"');
  }

  /** Full pipeline: markdown text → safe HTML string. */
  function format(text) {
    var raw = normalizeQuotes(text == null ? "" : String(text));
    if (!raw) return "";
    if (typeof marked === "undefined") {
      return escapeHtml(raw).replace(/\n/g, "<br>");
    }
    try {
      var opts = { breaks: true, gfm: true };
      var html = typeof marked.parse === "function"
        ? marked.parse(raw, opts)
        : marked(raw, opts);
      return wrapTables(renderMath(sanitize(html)));
    } catch (e) {
      return escapeHtml(raw).replace(/\n/g, "<br>");
    }
  }

  function highlightCode(root) {
    if (!root || typeof hljs === "undefined") return;
    var blocks = root.querySelectorAll("pre code");
    for (var i = 0; i < blocks.length; i++) {
      var el = blocks[i];
      if (el.dataset.highlighted) continue;
      try {
        hljs.highlightElement(el);
      } catch (e) {
        el.dataset.highlighted = "error";
      }
    }
  }

  var COPY_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var CHECK_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  function copyText(text, onDone) {
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        onDone();
      } catch (e) {}
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(onDone).catch(fallback);
    } else {
      fallback();
    }
  }

  function addCopyButtons(root) {
    if (!root) return;
    var nodes = root.querySelectorAll("pre, blockquote");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.dataset.copyAttached === "1") continue;
      el.dataset.copyAttached = "1";
      var wrap = document.createElement("div");
      wrap.className = "md-code-wrap";
      el.parentNode.insertBefore(wrap, el);
      wrap.appendChild(el);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "md-copy-btn";
      btn.title = "Copy";
      btn.setAttribute("aria-label", "Copy to clipboard");
      btn.innerHTML = COPY_ICON;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var target = this.parentNode.querySelector("pre, blockquote");
        var text = target ? target.innerText || target.textContent || "" : "";
        var self = this;
        copyText(text, function () {
          self.innerHTML = CHECK_ICON;
          setTimeout(function () {
            self.innerHTML = COPY_ICON;
          }, 1400);
        });
      });
      wrap.appendChild(btn);
    }
  }

  /** Render markdown into an element (idempotent via data-md). */
  function renderInto(el, text) {
    if (!el) return;
    el.innerHTML = format(text);
    el.classList.add("chat-prose");
    highlightCode(el);
    addCopyButtons(el);
    el.dataset.md = "1";
  }

  global.TomoMarkdown = {
    escapeHtml: escapeHtml,
    sanitize: sanitize,
    renderMath: renderMath,
    format: format,
    highlightCode: highlightCode,
    addCopyButtons: addCopyButtons,
    renderInto: renderInto,
  };
})(typeof window !== "undefined" ? window : this);

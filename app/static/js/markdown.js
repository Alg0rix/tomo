/* markdown.js — chat markdown: math → marked → sanitize → KaTeX → fences → mermaid → hljs.
 *
 * Math extracted BEFORE marked. Streaming uses formatPartial to close open fences.
 * Exposes window.TomoMarkdown.
 */
(function (global) {
  "use strict";

  var ALLOWED_TAGS = {
    a: 1, b: 1, i: 1, em: 1, strong: 1, code: 1, pre: 1, blockquote: 1,
    ul: 1, ol: 1, li: 1, p: 1, br: 1, hr: 1,
    h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1,
    table: 1, thead: 1, tbody: 1, tfoot: 1, tr: 1, th: 1, td: 1,
    span: 1, div: 1, img: 1, del: 1, s: 1, sub: 1, sup: 1, mark: 1,
    details: 1, summary: 1, input: 1, section: 1, figure: 1, figcaption: 1,
  };
  var ALLOWED_ATTRS = {
    a: ["href", "title", "target", "rel", "class", "aria-hidden", "id"],
    code: ["class"],
    pre: ["class", "data-lang", "data-pending"],
    span: ["class", "style", "aria-hidden", "data-i"],
    div: ["class", "data-callout", "style"],
    img: ["src", "alt", "class", "loading", "title"],
    ol: ["start", "class"],
    ul: ["class"],
    li: ["class", "id"],
    th: ["align", "style"],
    td: ["align", "style"],
    table: ["class"],
    input: ["type", "checked", "disabled", "class"],
    details: ["class", "open"],
    summary: ["class"],
    h1: ["id"], h2: ["id"], h3: ["id"], h4: ["id"], h5: ["id"], h6: ["id"],
    section: ["class", "data-footnotes"],
    blockquote: ["class", "data-callout"],
    figure: ["class"],
    figcaption: ["class"],
    mark: ["class"],
    del: ["class"],
    s: ["class"],
    sup: ["class", "id"],
    sub: ["class"],
  };

  var markedReady = false;
  var mermaidLoading = null;
  var lightboxBound = false;

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function slugify(text) {
    return String(text || "")
      .trim()
      .toLowerCase()
      .replace(/[^\w\u00c0-\u024f\s-]/g, "")
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "") || "section";
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
        var an = attrs[j].name;
        if (allowed.indexOf(an) === -1) {
          child.removeAttribute(an);
        }
      }
      if (tag === "input") {
        var type = (child.getAttribute("type") || "").toLowerCase();
        if (type !== "checkbox") {
          child.remove();
          continue;
        }
        child.setAttribute("type", "checkbox");
        child.setAttribute("disabled", "");
        child.classList.add("md-task-cb");
      }
      if (tag === "a") {
        var href = child.getAttribute("href") || "";
        if (/^javascript:/i.test(href) || /^data:/i.test(href)) {
          child.setAttribute("href", "#");
        }
        child.setAttribute("rel", "noopener noreferrer");
        if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) {
          child.setAttribute("target", "_blank");
        }
      }
      if (tag === "img") {
        var src = child.getAttribute("src") || "";
        if (!/^(https?:\/\/|\/|data:image\/)/i.test(src)) {
          child.removeAttribute("src");
        }
        child.setAttribute("loading", "lazy");
        child.classList.add("md-img");
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

  function isCurrencyLike(tex) {
    var body = String(tex || "").trim();
    if (!body) return true;
    if (/^\d/.test(body)) return true;
    if (/^[\d.,\s\-–—/~%]+$/.test(body)) return true;
    return false;
  }

  function looksLikeMath(tex) {
    var body = String(tex || "").trim();
    if (!body || isCurrencyLike(body)) return false;
    return /[a-zA-Z\\^_=<>+\-*/]|\\[a-zA-Z]+|[∑∫∞√πθαβγΔ∇∂±≤≥≠≈∈]/.test(body);
  }

  function normalizeTex(tex) {
    var t = String(tex || "").trim();
    t = t
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'");
    if (/\\\\[a-zA-Z]+/.test(t) && !/(^|[^\\])\\[a-zA-Z]+/.test(t)) {
      t = t.replace(/\\\\/g, "\\");
    }
    return t;
  }

  function katexOpts(display) {
    return {
      displayMode: !!display,
      throwOnError: false,
      strict: "ignore",
      trust: false,
      output: "html",
      minRuleThickness: 0.05,
    };
  }

  function renderTex(tex, display) {
    if (typeof katex === "undefined") {
      return '<code class="tomo-math-fallback">' + escapeHtml(tex) + "</code>";
    }
    try {
      var html = katex.renderToString(normalizeTex(tex), katexOpts(display));
      if (display) return '<div class="tomo-math-display">' + html + "</div>";
      return '<span class="tomo-math-inline">' + html + "</span>";
    } catch (e) {
      return '<code class="tomo-math-fallback">' + escapeHtml(tex) + "</code>";
    }
  }

  function extractMath(raw) {
    var blocks = [];
    var segs = [];
    var text = String(raw || "");

    function stashSeg(m) {
      segs.push(m);
      return "\0TOSEG" + (segs.length - 1) + "\0";
    }
    function stashMath(tex, display) {
      blocks.push({ tex: tex, display: !!display });
      return '<span class="tomo-math-ph" data-i="' + (blocks.length - 1) + '"></span>';
    }

    text = text.replace(/```[\s\S]*?```/g, stashSeg);
    text = text.replace(/~~~[\s\S]*?~~~/g, stashSeg);
    text = text.replace(/`[^`\n]+`/g, stashSeg);

    text = text.replace(/\$\$([\s\S]+?)\$\$/g, function (_m, tex) {
      return stashMath(tex, true);
    });
    text = text.replace(/\\\[([\s\S]+?)\\\]/g, function (_m, tex) {
      return stashMath(tex, true);
    });
    text = text.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, tex) {
      return stashMath(tex, false);
    });
    text = text.replace(/(?<!\$)\$(?!\$)([^\n$]+?)(?<!\$)\$(?!\$)/g, function (m, tex) {
      if (!looksLikeMath(tex)) return m;
      return stashMath(tex, false);
    });

    text = text.replace(/\0TOSEG(\d+)\0/g, function (_m, idx) {
      return segs[parseInt(idx, 10)] || _m;
    });

    return { text: text, blocks: blocks };
  }

  function injectMath(html, blocks) {
    if (!blocks || !blocks.length) return html;
    var out = String(html || "");
    out = out.replace(
      /<p>\s*<span class="tomo-math-ph" data-i="(\d+)"\s*><\/span>\s*<\/p>/g,
      function (_m, idx) {
        var block = blocks[parseInt(idx, 10)];
        if (!block) return _m;
        return renderTex(block.tex, true);
      }
    );
    out = out.replace(
      /<span class="tomo-math-ph" data-i="(\d+)"\s*><\/span>/g,
      function (_m, idx) {
        var block = blocks[parseInt(idx, 10)];
        if (!block) return _m;
        return renderTex(block.tex, block.display);
      }
    );
    return out;
  }

  function renderMath(html) {
    if (!html) return html || "";
    try {
      var stubs = [];
      function stash(m) {
        stubs.push(m);
        return "\0MATHSTUB" + (stubs.length - 1) + "\0";
      }
      var safe = String(html);
      safe = safe.replace(/<div\b[^>]*class="[^"]*table-wrapper[^"]*"[^>]*>[\s\S]*?<\/div>/gi, stash);
      safe = safe.replace(/<div\b[^>]*class="[^"]*tomo-math-display[^"]*"[^>]*>[\s\S]*?<\/div>/gi, stash);
      safe = safe.replace(/<span\b[^>]*class="[^"]*tomo-math-inline[^"]*"[^>]*>[\s\S]*?<\/span>/gi, stash);
      safe = safe.replace(/<table\b[\s\S]*?<\/table>/gi, stash);
      safe = safe.replace(/<pre\b[^>]*>[\s\S]*?<\/pre>/gi, stash);
      safe = safe.replace(/<code\b[^>]*>[\s\S]*?<\/code>/gi, stash);
      safe = safe.replace(/\$\$([\s\S]+?)\$\$/g, function (_m, f) { return renderTex(f, true); });
      safe = safe.replace(/\\\[([\s\S]+?)\\\]/g, function (_m, f) { return renderTex(f, true); });
      safe = safe.replace(/\\\(([\s\S]+?)\\\)/g, function (_m, f) { return renderTex(f, false); });
      safe = safe.replace(/(?<!\$)\$(?!\$)([^\$\n<>]+?)(?<!\$)\$(?!\$)/g, function (m, f) {
        if (!looksLikeMath(f)) return m;
        return renderTex(f, false);
      });
      safe = safe.replace(/\0MATHSTUB(\d+)\0/g, function (_m, idx) {
        return stubs[parseInt(idx, 10)] || _m;
      });
      return safe;
    } catch (e) {
      return html;
    }
  }

  /** Close incomplete fences / math for live streaming. */
  function closePartialMarkdown(raw) {
    var text = String(raw || "");
    var pending = { fence: false, math: false };
    var segs = [];
    var tmp = text.replace(/```[\s\S]*?```/g, function (m) {
      segs.push(m);
      return "\0S" + (segs.length - 1) + "\0";
    });
    // Odd number of ``` openers left?
    var opens = tmp.match(/```/g);
    if (opens && opens.length % 2 === 1) {
      text += "\n```";
      pending.fence = true;
    }
    // Unclosed $$
    var dollars = text.replace(/```[\s\S]*?```/g, "").match(/\$\$/g);
    if (dollars && dollars.length % 2 === 1) {
      text += "\n$$";
      pending.math = true;
    }
    return { text: text, pending: pending };
  }

  function preprocessCalloutsAndFootnotes(raw) {
    var text = String(raw || "");
    var footnotes = {};

    // Footnote defs: [^id]: body
    text = text.replace(/^\[\^([^\]]+)\]:\s+(.+(?:\n(?:[ \t]+.+))*)/gm, function (_m, id, body) {
      footnotes[id] = body.replace(/\n[ \t]+/g, " ").trim();
      return "";
    });

    // Footnote refs
    text = text.replace(/\[\^([^\]]+)\]/g, function (_m, id) {
      if (!footnotes[id]) return _m;
      return '<sup class="md-fn-ref"><a href="#fn-' + escapeHtml(id) + '" id="fnref-' + escapeHtml(id) + '">' + escapeHtml(id) + "</a></sup>";
    });

    var keys = Object.keys(footnotes);
    if (keys.length) {
      text += '\n\n<section class="md-footnotes" data-footnotes="1">\n<ol>\n';
      keys.forEach(function (id) {
        text +=
          '<li id="fn-' + escapeHtml(id) + '">' +
          footnotes[id] +
          ' <a href="#fnref-' + escapeHtml(id) + '" class="md-fn-back">↩</a></li>\n';
      });
      text += "</ol>\n</section>\n";
    }

    // GitHub-style callouts: > [!NOTE] title? (+ optional continuation lines)
    text = text.replace(
      /^> \[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION|INFO)\][ \t]*([^\n]*)(?:\n((?:>.*\n?)*))?/gim,
      function (_m, kind, title, body) {
        var lines = String(body || "")
          .split("\n")
          .map(function (ln) { return ln.replace(/^>[ \t]?/, ""); })
          .join("\n")
          .trim();
        var label = (title && String(title).trim()) || kind.charAt(0) + kind.slice(1).toLowerCase();
        return (
          '<div class="md-callout md-callout-' + kind.toLowerCase() + '" data-callout="' + kind.toLowerCase() + '">\n' +
          '<div class="md-callout-title">' + escapeHtml(label) + "</div>\n\n" +
          lines + "\n\n</div>\n\n"
        );
      }
    );

    return text;
  }

  function wrapTables(html) {
    return String(html || "").replace(/<table\b[\s\S]*?<\/table>/gi, function (table) {
      if (/class="[^"]*table-wrapper/.test(table)) return table;
      return '<div class="table-wrapper">' + table + "</div>";
    });
  }

  function enhanceTaskLists(html) {
    // Wrap ul/ol that contain checkbox inputs
    return String(html || "").replace(/<(ul|ol)>([\s\S]*?)<\/\1>/gi, function (m, tag, inner) {
      if (!/type="checkbox"/i.test(inner)) return m;
      var cleaned = inner.replace(/<li>/gi, '<li class="task-list-item">');
      return "<" + tag + ' class="task-list">' + cleaned + "</" + tag + ">";
    });
  }

  function enhanceAlign(html) {
    // marked emits align="left|center|right" — also mirror as style for sticky tables
    return String(html || "").replace(/\salign="(left|center|right)"/gi, function (_m, a) {
      return ' align="' + a + '" style="text-align:' + a + '"';
    });
  }

  function normalizeQuotes(text) {
    return String(text || "").replace(/[\u201c\u201d\u00ab\u00bb]/g, '"');
  }

  function ensureMarked() {
    if (markedReady || typeof marked === "undefined") return;
    markedReady = true;
    var api = marked.marked || marked;
    if (typeof api.use !== "function") return;
    api.use({
      renderer: {
        code: function (token) {
          var text = typeof token === "string" ? token : (token.text || "");
          var lang = (typeof token === "string" ? arguments[1] : token.lang) || "";
          lang = String(lang || "").trim().split(/\s+/)[0];
          var langClass = lang ? "language-" + lang : "";
          var isMermaid = /^mermaid$/i.test(lang);
          if (isMermaid) {
            return (
              '<div class="md-mermaid" data-mermaid="1">' +
              '<pre class="mermaid">' + escapeHtml(text) + "</pre>" +
              "</div>\n"
            );
          }
          return (
            '<pre class="md-pre" data-lang="' + escapeHtml(lang || "text") + '">' +
            '<code class="' + escapeHtml(langClass) + '">' + escapeHtml(text) + "</code>" +
            "</pre>\n"
          );
        },
        heading: function (token) {
          var depth = token.depth || 2;
          var text = token.text || "";
          var body =
            token.tokens && this.parser
              ? this.parser.parseInline(token.tokens)
              : escapeHtml(text);
          var id = slugify(text);
          return (
            "<h" + depth + ' id="' + id + '">' +
            '<a class="md-h-anchor" href="#' + id + '" aria-hidden="true">#</a>' +
            body +
            "</h" + depth + ">\n"
          );
        },
      },
    });
  }

  function parseMarkdown(raw) {
    ensureMarked();
    var api = typeof marked !== "undefined" ? (marked.marked || marked) : null;
    if (!api) return escapeHtml(raw).replace(/\n/g, "<br>");
    var opts = { breaks: true, gfm: true };
    if (typeof api.parse === "function") return api.parse(raw, opts);
    return api(raw, opts);
  }

  /** Full pipeline: markdown text → safe HTML string. */
  function format(text, opts) {
    opts = opts || {};
    var raw = normalizeQuotes(text == null ? "" : String(text));
    if (!raw) return "";
    if (opts.partial) {
      raw = closePartialMarkdown(raw).text;
    }
    try {
      raw = preprocessCalloutsAndFootnotes(raw);
      var extracted = extractMath(raw);
      var html = parseMarkdown(extracted.text);
      html = enhanceAlign(html);
      html = wrapTables(html);
      html = enhanceTaskLists(html);
      html = sanitize(html);
      html = injectMath(html, extracted.blocks);
      return html;
    } catch (e) {
      return escapeHtml(String(text || "")).replace(/\n/g, "<br>");
    }
  }

  function highlightCode(root) {
    if (!root || typeof hljs === "undefined") return;
    var blocks = root.querySelectorAll("pre code");
    for (var i = 0; i < blocks.length; i++) {
      var el = blocks[i];
      if (el.dataset.highlighted) continue;
      // Skip mermaid source blocks
      if (el.closest && el.closest(".mermaid, .md-mermaid")) continue;
      try {
        var lang = "";
        var pre = el.parentElement;
        if (pre && pre.getAttribute) lang = pre.getAttribute("data-lang") || "";
        if (/^diff$/i.test(lang) && hljs.getLanguage && hljs.getLanguage("diff")) {
          el.classList.add("language-diff");
        }
        hljs.highlightElement(el);
      } catch (e) {
        el.dataset.highlighted = "error";
      }
    }
  }

  var COPY_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var CHECK_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
  var WRAP_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M3 12h12a3 3 0 1 1 0 6h-4"/><path d="m11 15-3 3 3 3"/></svg>';

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

  function enhanceCodeBlocks(root) {
    if (!root) return;
    var pres = root.querySelectorAll("pre.md-pre, pre:not(.mermaid)");
    for (var i = 0; i < pres.length; i++) {
      var pre = pres[i];
      if (pre.dataset.chrome === "1") continue;
      if (pre.classList.contains("mermaid")) continue;
      pre.dataset.chrome = "1";
      if (!pre.classList.contains("md-pre")) pre.classList.add("md-pre");

      var lang = pre.getAttribute("data-lang") || "";
      if (!lang) {
        var code = pre.querySelector("code");
        var m = code && (code.className || "").match(/language-([\w+-]+)/);
        lang = m ? m[1] : "text";
        pre.setAttribute("data-lang", lang);
      }

      var wrap = document.createElement("div");
      wrap.className = "md-code-wrap";
      if (pre.classList.contains("md-pre-pending") || pre.getAttribute("data-pending") === "1") {
        wrap.classList.add("md-code-pending");
      }
      pre.parentNode.insertBefore(wrap, pre);

      var bar = document.createElement("div");
      bar.className = "md-code-bar";
      bar.innerHTML =
        '<span class="md-code-lang">' + escapeHtml(lang) + "</span>" +
        '<span class="md-code-actions"></span>';
      var actions = bar.querySelector(".md-code-actions");

      var wrapBtn = document.createElement("button");
      wrapBtn.type = "button";
      wrapBtn.className = "md-code-btn md-wrap-btn";
      wrapBtn.title = "Toggle wrap";
      wrapBtn.setAttribute("aria-label", "Toggle line wrap");
      wrapBtn.innerHTML = WRAP_ICON;
      wrapBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        wrap.classList.toggle("is-wrapped");
      });

      var copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "md-code-btn md-copy-btn";
      copyBtn.title = "Copy";
      copyBtn.setAttribute("aria-label", "Copy code");
      copyBtn.innerHTML = COPY_ICON;
      copyBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        var target = wrap.querySelector("pre code, pre");
        var text = target ? target.innerText || target.textContent || "" : "";
        var self = this;
        copyText(text, function () {
          self.innerHTML = CHECK_ICON;
          self.classList.add("ok");
          setTimeout(function () {
            self.innerHTML = COPY_ICON;
            self.classList.remove("ok");
          }, 1400);
        });
      });

      actions.appendChild(wrapBtn);
      actions.appendChild(copyBtn);
      wrap.appendChild(bar);
      wrap.appendChild(pre);
    }
  }

  function themeIsDark() {
    var t = document.documentElement.getAttribute("data-theme") || "dark";
    return t !== "light";
  }

  function ensureMermaid(cb) {
    if (global.mermaid) {
      cb(null, global.mermaid);
      return;
    }
    if (mermaidLoading) {
      mermaidLoading.then(
        function () { cb(null, global.mermaid); },
        function (err) { cb(err); }
      );
      return;
    }
    mermaidLoading = new Promise(function (resolve, reject) {
      function load(src, next) {
        var s = document.createElement("script");
        s.src = src;
        s.async = true;
        s.onload = function () { next(null); };
        s.onerror = function () { next(new Error("load failed: " + src)); };
        document.head.appendChild(s);
      }
      load("/static/js/vendor/mermaid.min.js", function (err) {
        if (!err && global.mermaid) {
          resolve(global.mermaid);
          return;
        }
        load("https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js", function (err2) {
          if (err2 || !global.mermaid) reject(err2 || new Error("mermaid unavailable"));
          else resolve(global.mermaid);
        });
      });
    });
    mermaidLoading.then(
      function (m) { cb(null, m); },
      function (err) { cb(err); }
    );
  }

  function renderMermaid(root) {
    if (!root) return;
    var nodes = root.querySelectorAll(".md-mermaid pre.mermaid, pre.mermaid");
    if (!nodes.length) return;
    ensureMermaid(function (err, mermaid) {
      if (err || !mermaid) {
        for (var i = 0; i < nodes.length; i++) {
          nodes[i].parentElement.classList.add("md-mermaid-error");
        }
        return;
      }
      try {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: themeIsDark() ? "dark" : "default",
          fontFamily: "inherit",
        });
      } catch (e) {}
      var list = [];
      for (var j = 0; j < nodes.length; j++) {
        var el = nodes[j];
        if (el.dataset.rendered === "1") continue;
        el.dataset.rendered = "1";
        if (!el.id) el.id = "mermaid-" + Math.random().toString(36).slice(2, 9);
        list.push(el);
      }
      if (!list.length) return;
      if (typeof mermaid.run === "function") {
        mermaid.run({ nodes: list }).catch(function () {});
      } else if (typeof mermaid.init === "function") {
        mermaid.init(undefined, list);
      }
    });
  }

  function enhanceImages(root) {
    if (!root) return;
    var imgs = root.querySelectorAll("img.md-img, .chat-prose img");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.dataset.enhanced === "1") continue;
      img.dataset.enhanced = "1";
      img.classList.add("md-img");
      img.addEventListener("error", function () {
        this.classList.add("md-img-broken");
        this.alt = this.alt || "Image failed to load";
      });
      img.addEventListener("click", function (e) {
        e.preventDefault();
        openLightbox(this.getAttribute("src"), this.getAttribute("alt") || "");
      });
    }
  }

  function openLightbox(src, alt) {
    if (!src) return;
    ensureLightbox();
    var lb = document.getElementById("mdLightbox");
    if (!lb) return;
    var img = lb.querySelector(".md-lightbox-img");
    var cap = lb.querySelector(".md-lightbox-cap");
    var open = lb.querySelector(".md-lightbox-open");
    img.src = src;
    img.alt = alt || "";
    cap.textContent = alt || "";
    open.href = src;
    lb.classList.remove("hidden");
    lb.setAttribute("aria-hidden", "false");
  }

  function ensureLightbox() {
    if (document.getElementById("mdLightbox")) {
      if (!lightboxBound) bindLightbox();
      return;
    }
    var lb = document.createElement("div");
    lb.id = "mdLightbox";
    lb.className = "md-lightbox hidden";
    lb.setAttribute("role", "dialog");
    lb.setAttribute("aria-modal", "true");
    lb.setAttribute("aria-hidden", "true");
    lb.innerHTML =
      '<div class="md-lightbox-backdrop" data-close="1"></div>' +
      '<div class="md-lightbox-card">' +
        '<button type="button" class="md-lightbox-x" data-close="1" aria-label="Close">×</button>' +
        '<img class="md-lightbox-img" alt="">' +
        '<div class="md-lightbox-foot">' +
          '<span class="md-lightbox-cap"></span>' +
          '<a class="md-lightbox-open" href="#" target="_blank" rel="noopener noreferrer">Open</a>' +
        "</div>" +
      "</div>";
    document.body.appendChild(lb);
    bindLightbox();
  }

  function bindLightbox() {
    if (lightboxBound) return;
    lightboxBound = true;
    document.addEventListener("click", function (e) {
      var lb = document.getElementById("mdLightbox");
      if (!lb || lb.classList.contains("hidden")) return;
      if (e.target && e.target.closest && e.target.closest("[data-close]")) {
        lb.classList.add("hidden");
        lb.setAttribute("aria-hidden", "true");
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var lb = document.getElementById("mdLightbox");
      if (!lb || lb.classList.contains("hidden")) return;
      lb.classList.add("hidden");
      lb.setAttribute("aria-hidden", "true");
    });
  }

  /** Render markdown into an element (idempotent via data-md). */
  function renderInto(el, text, opts) {
    if (!el) return;
    opts = opts || {};
    var partial = !!opts.partial || !!(el.closest && el.closest(".streaming, .msg.streaming"));
    var closed = partial ? closePartialMarkdown(text == null ? "" : String(text)) : null;
    el.innerHTML = format(closed ? closed.text : text, { partial: false });
    el.classList.add("chat-prose");
    if (partial && closed && closed.pending.fence) {
      var pres = el.querySelectorAll("pre.md-pre, pre");
      if (pres.length) {
        var last = pres[pres.length - 1];
        last.classList.add("md-pre-pending");
        last.setAttribute("data-pending", "1");
      }
    }
    highlightCode(el);
    enhanceCodeBlocks(el);
    enhanceImages(el);
    if (!partial) renderMermaid(el);
    el.dataset.md = partial ? "partial" : "1";
  }

  global.TomoMarkdown = {
    escapeHtml: escapeHtml,
    sanitize: sanitize,
    renderMath: renderMath,
    extractMath: extractMath,
    format: format,
    formatPartial: function (text) { return format(text, { partial: true }); },
    highlightCode: highlightCode,
    enhanceCodeBlocks: enhanceCodeBlocks,
    renderMermaid: renderMermaid,
    enhanceImages: enhanceImages,
    openLightbox: openLightbox,
    render: renderInto,
    renderInto: renderInto,
  };
})(typeof window !== "undefined" ? window : this);

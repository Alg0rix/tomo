/* generative_ui.js — safe declarative UI renderer for agent-generated trees. */
(function (global) {
  "use strict";

  var ROOT_CLASS = "gen-ui";

  function esc(value) {
    return global.Tomo && Tomo.escapeHtml
      ? Tomo.escapeHtml(value == null ? "" : String(value))
      : String(value == null ? "" : value);
  }

  function safeUrl(value) {
    var url = String(value || "").trim();
    if (/^https?:\/\//i.test(url) || /^\/(?!\/)/.test(url)) return url;
    if (/^data:image\/(?:png|gif|jpe?g|webp|svg\+xml);/i.test(url)) return url;
    return "";
  }

  function collectValues(root) {
    var payload = {};
    root.querySelectorAll("[data-gen-bind]").forEach(function (el) {
      payload[el.getAttribute("data-gen-bind")] = el.value;
    });
    return payload;
  }

  function sendAction(root, ctx, action, extra) {
    if (!action) return;
    var payload = Object.assign(collectValues(root), extra || {});
    if (typeof ctx.onAction === "function") {
      ctx.onAction(action, payload, root.getAttribute("data-ui-id"));
      return;
    }
    root.dispatchEvent(new CustomEvent("tomo:ui-action", {
      bubbles: true,
      detail: { ui_id: root.getAttribute("data-ui-id"), action: action, payload: payload },
    }));
  }

  function textNode(value, className) {
    var el = document.createElement("div");
    if (className) el.className = className;
    el.textContent = value == null ? "" : String(value);
    return el;
  }

  function addChildren(el, node, ctx) {
    (node.children || []).forEach(function (child) {
      var rendered = renderNode(child, ctx);
      if (rendered) el.appendChild(rendered);
    });
  }

  function renderNode(node, ctx) {
    if (!node || typeof node !== "object") return null;
    var type = String(node.type || "").toLowerCase();
    var el;

    if (type === "text") return textNode(node.value, "gen-ui-text");
    if (type === "markdown") {
      el = document.createElement("div");
      el.className = "gen-ui-markdown chat-prose";
      if (global.TomoMarkdown && TomoMarkdown.renderInto) {
        TomoMarkdown.renderInto(el, node.value || "");
      } else {
        el.textContent = node.value || "";
      }
      return el;
    }
    if (type === "badge") return textNode(node.value, "gen-ui-badge " + (node.variant || ""));
    if (type === "divider") {
      el = document.createElement("hr");
      el.className = "gen-ui-divider";
      return el;
    }
    if (type === "stack" || type === "grid" || type === "card") {
      el = document.createElement(type === "card" ? "section" : "div");
      el.className = "gen-ui-node gen-ui-" + type;
      if (node.title) el.appendChild(textNode(node.title, "gen-ui-title"));
      if (node.description) el.appendChild(textNode(node.description, "gen-ui-description"));
      addChildren(el, node, ctx);
      return el;
    }
    if (type === "table") {
      el = document.createElement("div");
      el.className = "gen-ui-table-wrap";
      var table = document.createElement("table");
      var head = document.createElement("thead");
      var hr = document.createElement("tr");
      (node.columns || []).forEach(function (column) {
        var th = document.createElement("th"); th.textContent = column; hr.appendChild(th);
      });
      head.appendChild(hr); table.appendChild(head);
      var body = document.createElement("tbody");
      (node.rows || []).forEach(function (row) {
        var tr = document.createElement("tr");
        row.forEach(function (cell) { var td = document.createElement("td"); td.textContent = cell; tr.appendChild(td); });
        body.appendChild(tr);
      });
      table.appendChild(body); el.appendChild(table); return el;
    }
    if (type === "chart") {
      el = document.createElement("div");
      el.className = "gen-ui-chart";
      var points = node.data || [];
      var max = points.reduce(function (acc, p) { return Math.max(acc, Math.abs(Number(p.value) || 0)); }, 0) || 1;
      points.forEach(function (point) {
        var row = document.createElement("div"); row.className = "gen-ui-chart-row";
        var label = textNode(point.label, "gen-ui-chart-label");
        var track = document.createElement("div"); track.className = "gen-ui-chart-track";
        var bar = document.createElement("div"); bar.className = "gen-ui-chart-bar";
        bar.style.width = Math.max(2, Math.round(Math.abs(Number(point.value) || 0) / max * 100)) + "%";
        track.appendChild(bar); row.appendChild(label); row.appendChild(track);
        row.appendChild(textNode(point.value, "gen-ui-chart-value")); el.appendChild(row);
      });
      return el;
    }
    if (type === "mermaid") {
      el = document.createElement("div");
      el.className = "gen-ui-mermaid";
      if (global.TomoMarkdown && TomoMarkdown.renderInto) {
        TomoMarkdown.renderInto(el, "```mermaid\n" + (node.value || "") + "\n```");
      } else el.textContent = node.value || "";
      return el;
    }
    if (type === "image") {
      var src = safeUrl(node.src);
      if (!src) return textNode("Blocked image URL", "gen-ui-error");
      el = document.createElement("img"); el.className = "gen-ui-image"; el.src = src; el.alt = node.alt || ""; el.loading = "lazy"; return el;
    }
    if (type === "link") {
      var href = safeUrl(node.href);
      if (!href) return textNode(node.label || node.href || "Blocked link", "gen-ui-error");
      el = document.createElement("a"); el.className = "gen-ui-link"; el.href = href; el.target = "_blank"; el.rel = "noopener noreferrer"; el.textContent = node.label || href; return el;
    }
    if (type === "input") {
      el = document.createElement("label"); el.className = "gen-ui-field";
      if (node.label) el.appendChild(textNode(node.label, "gen-ui-field-label"));
      var input = document.createElement("input"); input.type = "text"; input.placeholder = node.placeholder || ""; input.value = node.value || ""; input.disabled = !!node.disabled; input.setAttribute("data-gen-bind", node.id); el.appendChild(input);
      if (node.action) input.addEventListener("change", function () { sendAction(rootFor(input), ctx, node.action); });
      return el;
    }
    if (type === "select") {
      el = document.createElement("label"); el.className = "gen-ui-field";
      if (node.label) el.appendChild(textNode(node.label, "gen-ui-field-label"));
      var select = document.createElement("select"); select.disabled = !!node.disabled; select.setAttribute("data-gen-bind", node.id);
      (node.options || []).forEach(function (option) { var o = document.createElement("option"); o.value = option.value; o.textContent = option.label; select.appendChild(o); });
      el.appendChild(select); if (node.action) select.addEventListener("change", function () { sendAction(rootFor(select), ctx, node.action); }); return el;
    }
    if (type === "button") {
      el = document.createElement("button"); el.type = "button"; el.className = "gen-ui-button " + (node.variant || ""); el.textContent = node.label || "Action"; el.disabled = !!node.disabled;
      el.addEventListener("click", function () { sendAction(rootFor(el), ctx, node.action); }); return el;
    }
    return null;
  }

  function rootFor(el) {
    return el.closest("." + ROOT_CLASS) || el.parentElement;
  }

  function findExisting(parent, uiId) {
    var found = null;
    parent.querySelectorAll("." + ROOT_CLASS).forEach(function (el) {
      if (el.getAttribute("data-ui-id") === uiId) found = el;
    });
    return found;
  }

  function mount(parent, spec, opts) {
    if (!parent || !spec || !spec.tree) return null;
    opts = opts || {};
    var uiId = String(spec.ui_id || spec.id || "");
    if (!uiId) return null;
    var root = findExisting(parent, uiId);
    if (!root) {
      root = document.createElement("div");
      root.className = ROOT_CLASS;
      root.setAttribute("data-ui-id", uiId);
      parent.appendChild(root);
    }
    root.replaceChildren();
    var onAction = opts.onAction;
    if (!onAction && typeof opts.send === "function") {
      onAction = function (action, payload, id) {
        return opts.send({ ui_id: id || uiId, action: action, payload: payload });
      };
    }
    var rendered = renderNode(spec.tree, { onAction: onAction });
    if (rendered) root.appendChild(rendered);
    return root;
  }

  global.TomoGenerativeUI = { mount: mount, renderNode: renderNode };
})(typeof window !== "undefined" ? window : this);

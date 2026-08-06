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

  function clone(value) {
    if (value == null) return value;
    try { return JSON.parse(JSON.stringify(value)); } catch (_) { return null; }
  }

  function storageKey(sessionId, uiId) {
    var sid = String(sessionId || "local");
    return "tomo:gen-ui:v2:" + encodeURIComponent(sid) + ":" + encodeURIComponent(uiId);
  }

  function getStorage() {
    try { return global.sessionStorage || null; } catch (_) { return null; }
  }

  function readPersisted(sessionId, uiId) {
    var storage = getStorage();
    if (!storage) return null;
    try {
      var raw = storage.getItem(storageKey(sessionId, uiId));
      var value = raw ? JSON.parse(raw) : null;
      return value && value.tree ? value : null;
    } catch (_) { return null; }
  }

  function persist(root) {
    var storage = getStorage();
    if (!root || !root._genSpec || !root._genSpec.tree || !storage) return;
    try {
      storage.setItem(root._genStorageKey, JSON.stringify({
        ui_id: root._genSpec.ui_id,
        tree: root._genSpec.tree,
        state: root._genSpec.state || {},
      }));
    } catch (_) { /* storage is best effort (quota/private mode) */ }
  }

  function pointerParts(path) {
    if (typeof path !== "string" || path.charAt(0) !== "/") return null;
    return path.slice(1).split("/").map(function (part) {
      return part.replace(/~1/g, "/").replace(/~0/g, "~");
    });
  }

  function arrayIndex(value, length, allowEnd) {
    if (!/^\d+$/.test(value)) return -1;
    var index = Number(value);
    if (!Number.isSafeInteger(index) || index < 0) return -1;
    if (index > length || (!allowEnd && index === length)) return -1;
    return index;
  }

  // Apply the small JSON-Patch subset accepted by the server. Keeping this
  // implementation local means the browser never evaluates model-provided
  // code or HTML; only data under /tree and /state is changed.
  function applyPatch(document, operation) {
    if (!operation || typeof operation !== "object") return false;
    var parts = pointerParts(operation.path);
    var op = String(operation.op || "").toLowerCase();
    if (!parts || !parts.length || (parts[0] !== "tree" && parts[0] !== "state")) return false;
    if (["add", "replace", "remove"].indexOf(op) < 0) return false;
    var base = parts.shift();
    if (!parts.length) {
      if (op === "remove") delete document[base];
      else document[base] = clone(operation.value);
      return true;
    }
    var parent = document[base];
    if (parent == null || (typeof parent !== "object")) return false;
    for (var i = 0; i < parts.length - 1; i++) {
      var part = parts[i];
      if (Array.isArray(parent)) {
        var nestedIndex = arrayIndex(part, parent.length, false);
        if (nestedIndex < 0) return false;
        parent = parent[nestedIndex];
      } else {
        if (!Object.prototype.hasOwnProperty.call(parent, part)) return false;
        parent = parent[part];
      }
      if (parent == null || typeof parent !== "object") return false;
    }
    var leaf = parts[parts.length - 1];
    if (Array.isArray(parent)) {
      var index = arrayIndex(leaf, parent.length, op === "add");
      if (index < 0) return false;
      if (op === "add") parent.splice(index, 0, clone(operation.value));
      else if (op === "replace") {
        if (index >= parent.length) return false;
        parent[index] = clone(operation.value);
      } else {
        if (index >= parent.length) return false;
        parent.splice(index, 1);
      }
      return true;
    }
    if (op === "remove") {
      if (!Object.prototype.hasOwnProperty.call(parent, leaf)) return false;
      delete parent[leaf];
    } else if (op === "replace") {
      if (!Object.prototype.hasOwnProperty.call(parent, leaf)) return false;
      parent[leaf] = clone(operation.value);
    } else {
      parent[leaf] = clone(operation.value);
    }
    return true;
  }

  function materialize(parent, spec, root, opts, uiId) {
    var sessionId = opts.sessionId || (parent.closest && (parent.closest("[data-session-id]") || {}).dataset || {}).sessionId || "";
    var prior = root._genSpec || readPersisted(sessionId, uiId);
    var current = prior && prior.tree ? clone(prior) : { ui_id: uiId, tree: null, state: {} };
    current.ui_id = uiId;
    if (!current.state || typeof current.state !== "object" || Array.isArray(current.state)) current.state = {};
    var hasTree = Object.prototype.hasOwnProperty.call(spec, "tree") && spec.tree != null;
    var hasState = Object.prototype.hasOwnProperty.call(spec, "state") && spec.state != null;
    var mode = spec.mode || "replace";

    if (mode === "replace") {
      if (hasTree) current.tree = clone(spec.tree);
      if (hasState) {
        // Treat replace.state as defaults. Locally edited controls are kept
        // when history is replayed after a refresh; an explicit /state patch
        // remains the way for the agent to overwrite a value.
        current.state = Object.assign({}, clone(spec.state) || {}, current.state || {});
      }
    } else if (mode === "patch") {
      // A patch may carry a tree as a bootstrap/base for a new UI instance.
      if (hasTree) current.tree = clone(spec.tree);
      if (hasState) current.state = Object.assign({}, current.state, clone(spec.state) || {});
      var working = clone(current);
      var operations = Array.isArray(spec.patch) ? spec.patch : [];
      for (var i = 0; i < operations.length; i++) {
        if (!applyPatch(working, operations[i])) return root._genSpec ? root : null;
      }
      current = working;
    } else {
      return root._genSpec ? root : null;
    }
    if (!current.tree || typeof current.tree !== "object") return root._genSpec ? root : null;
    root._genSpec = current;
    root._genState = current.state;
    root._genStorageKey = storageKey(sessionId, uiId);
    return root;
  }

  function syncBoundValues(root, ctx) {
    if (!root || !ctx || !ctx.state) return;
    root.querySelectorAll("[data-gen-bind]").forEach(function (el) {
      var id = el.getAttribute("data-gen-bind");
      if (id) ctx.state[id] = el.value;
    });
    if (ctx.persist) ctx.persist();
  }

  function sendAction(root, ctx, action, extra) {
    if (!action) return;
    syncBoundValues(root, ctx);
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
      var input = document.createElement("input"); input.type = "text"; input.placeholder = node.placeholder || "";
      var inputValue = ctx.state && Object.prototype.hasOwnProperty.call(ctx.state, node.id) ? ctx.state[node.id] : node.value;
      input.value = inputValue == null ? "" : String(inputValue); input.disabled = !!node.disabled; input.setAttribute("data-gen-bind", node.id); el.appendChild(input);
      input.addEventListener("input", function () { if (ctx.state) ctx.state[node.id] = input.value; if (ctx.persist) ctx.persist(); });
      if (node.action) input.addEventListener("change", function () { sendAction(rootFor(input), ctx, node.action); });
      return el;
    }
    if (type === "select") {
      el = document.createElement("label"); el.className = "gen-ui-field";
      if (node.label) el.appendChild(textNode(node.label, "gen-ui-field-label"));
      var select = document.createElement("select"); select.disabled = !!node.disabled; select.setAttribute("data-gen-bind", node.id);
      (node.options || []).forEach(function (option) { var o = document.createElement("option"); o.value = option.value; o.textContent = option.label; select.appendChild(o); });
      var selectedValue = ctx.state && Object.prototype.hasOwnProperty.call(ctx.state, node.id) ? ctx.state[node.id] : node.value;
      if (selectedValue != null) select.value = String(selectedValue);
      select.addEventListener("change", function () { if (ctx.state) ctx.state[node.id] = select.value; if (ctx.persist) ctx.persist(); if (node.action) sendAction(rootFor(select), ctx, node.action); });
      el.appendChild(select); return el;
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
    if (!parent || !spec) return null;
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
    if (!materialize(parent, spec, root, opts, uiId)) return null;
    root.replaceChildren();
    var onAction = opts.onAction;
    var dispatcher = opts.dispatch || opts.send;
    if (!onAction && typeof dispatcher === "function") {
      onAction = function (action, payload, id) {
        return dispatcher({ ui_id: id || uiId, action: action, payload: payload });
      };
    }
    var eventCount = Number(root.dataset.uiEvents || 0) + 1;
    root.dataset.uiEvents = String(eventCount);
    var rendered = renderNode(root._genSpec.tree, {
      onAction: onAction,
      state: root._genState,
      persist: function () { persist(root); },
    });
    if (rendered) root.appendChild(rendered);
    else root.appendChild(textNode("UI tree could not be rendered", "gen-ui-error"));
    persist(root);
    return root;
  }

  global.TomoGenerativeUI = { mount: mount, renderNode: renderNode };
})(typeof window !== "undefined" ? window : this);

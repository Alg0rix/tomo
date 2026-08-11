/* browser_bridge.js — Tomo web ↔ Chrome extension ↔ backend gateway.
 *
 * Discovery: chrome.runtime.sendMessage(extensionId, TOMO_PING)
 * Session:   POST /api/browser/sessions + WS /api/browser/ws
 * Execute:   WS browser.tool.execute → extension → WS browser.tool.result
 *
 * UX: status chip inside the composer dock (no floating HUD).
 */
(function () {
  "use strict";

  var PROTOCOL = "tomo.browser.v1";
  var HEARTBEAT_MS = 20000;

  var ACTIVITY = {
    browser_tabs: "Listing tabs",
    browser_attach: "Attaching to tab",
    browser_snapshot: "Reading page",
    browser_click: "Clicking",
    browser_type: "Typing",
    browser_press: "Pressing key",
    browser_select: "Selecting option",
    browser_scroll: "Scrolling",
    browser_navigate: "Navigating",
    browser_back: "Going back",
    browser_forward: "Going forward",
    browser_wait: "Waiting",
    browser_screenshot: "Taking screenshot",
    browser_extract: "Extracting text",
  };

  function BrowserBridge(opts) {
    opts = opts || {};
    this.extensionId = opts.extensionId || "";
    this.status = "unsupported";
    this.sessionId = null;
    this.capabilities = [];
    this.tabs = [];
    this.extensionVersion = "";
    this._ws = null;
    this._heartbeatTimer = null;
    this._listeners = {};
    this._clientId = this._loadClientId();
    /** @type {Map<string, {tool:string, label:string, started:number}>} */
    this._active = new Map();
    this._chip = null;
    this._panel = null;
    this._popoverOpen = false;
  }

  BrowserBridge.prototype._loadClientId = function () {
    try {
      var id = localStorage.getItem("tomo-browser-client-id");
      if (id) return id;
      id = "client_" + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 12) : String(Date.now()));
      localStorage.setItem("tomo-browser-client-id", id);
      return id;
    } catch (e) {
      return "client_" + String(Date.now());
    }
  };

  BrowserBridge.prototype.on = function (event, fn) {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(fn);
  };

  BrowserBridge.prototype._emit = function (event, data) {
    var list = this._listeners[event] || [];
    for (var i = 0; i < list.length; i++) {
      try { list[i](data); } catch (e) {}
    }
  };

  BrowserBridge.prototype.isControlling = function () {
    return this._active.size > 0;
  };

  BrowserBridge.prototype.currentActivity = function () {
    if (!this._active.size) return "";
    var last = null;
    this._active.forEach(function (v) { last = v; });
    return (last && last.label) || "Working…";
  };

  BrowserBridge.prototype._setStatus = function (status) {
    this.status = status;
    this._emit("status", {
      status: status,
      sessionId: this.sessionId,
      tabs: this.tabs,
      capabilities: this.capabilities,
      controlling: this.isControlling(),
      activity: this.currentActivity(),
    });
    this._renderChip();
    if (this._popoverOpen) this._renderPanel();
  };

  BrowserBridge.prototype._setActive = function (callId, tool, args, on) {
    var id = callId || ("anon_" + tool + "_" + Date.now());
    if (on) {
      this._active.set(id, {
        tool: tool || "browser",
        label: ACTIVITY[tool] || "Controlling browser",
        args: args || {},
        started: Date.now(),
      });
    } else {
      this._active.delete(id);
      if (!callId && tool) {
        var self = this;
        this._active.forEach(function (v, k) {
          if (v.tool === tool) self._active.delete(k);
        });
      }
    }
    this._emit("activity", {
      controlling: this.isControlling(),
      activity: this.currentActivity(),
      active: this._active.size,
    });
    this._renderChip();
    if (this._popoverOpen) this._renderPanel();
  };

  BrowserBridge.prototype.noteAgentTool = function (tool, args, callId, running) {
    if (String(tool || "").indexOf("browser_") !== 0) return;
    this._setActive(callId || tool, tool, args || {}, !!running);
  };

  BrowserBridge.prototype.hasChromeRuntime = function () {
    return typeof chrome !== "undefined" && chrome.runtime && typeof chrome.runtime.sendMessage === "function";
  };

  BrowserBridge.prototype.ping = function () {
    var self = this;
    return new Promise(function (resolve) {
      if (!self.hasChromeRuntime()) {
        self._setStatus("unsupported");
        resolve({ ok: false, status: "unsupported" });
        return;
      }
      if (!self.extensionId) {
        self._setStatus("not_installed");
        resolve({ ok: false, status: "not_installed", reason: "missing_extension_id" });
        return;
      }
      var nonce = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
      try {
        chrome.runtime.sendMessage(
          self.extensionId,
          { protocol: PROTOCOL, type: "TOMO_PING", nonce: nonce },
          function (response) {
            var err = chrome.runtime.lastError;
            if (err || !response) {
              self._setStatus("not_installed");
              resolve({ ok: false, status: "not_installed", error: err && err.message });
              return;
            }
            if (response.type !== "TOMO_PONG" || response.protocol !== PROTOCOL) {
              self._setStatus("error");
              resolve({ ok: false, status: "error", error: "bad_pong" });
              return;
            }
            self.extensionVersion = (response.extension && response.extension.version) || "0.1.0";
            self.capabilities = response.capabilities || [];
            self._setStatus("installed");
            resolve({
              ok: true,
              status: "installed",
              version: self.extensionVersion,
              capabilities: self.capabilities,
              browser: response.browser || { name: "chrome" },
            });
          }
        );
      } catch (e) {
        self._setStatus("error");
        resolve({ ok: false, status: "error", error: String(e) });
      }
    });
  };

  BrowserBridge.prototype.sendToExtension = function (message) {
    var self = this;
    return new Promise(function (resolve, reject) {
      if (!self.hasChromeRuntime() || !self.extensionId) {
        reject(new Error("extension unavailable"));
        return;
      }
      try {
        chrome.runtime.sendMessage(self.extensionId, message, function (response) {
          var err = chrome.runtime.lastError;
          if (err) {
            reject(new Error(err.message || "extension error"));
            return;
          }
          resolve(response);
        });
      } catch (e) {
        reject(e);
      }
    });
  };

  BrowserBridge.prototype.connect = function () {
    var self = this;
    self._setStatus("connecting");
    return self.ping().then(function (pong) {
      if (!pong.ok) {
        self._setStatus(pong.status || "not_installed");
        return pong;
      }
      return Tomo.api("/api/browser/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: self._clientId,
          extension_version: self.extensionVersion || "0.1.0",
          capabilities: self.capabilities || [],
        }),
      }).then(function (data) {
        if (!data || !data.session_id) {
          self._setStatus("error");
          return { ok: false, status: "error" };
        }
        self.sessionId = data.session_id;
        if (data.capabilities) self.capabilities = data.capabilities;
        return self._openWs().then(function () {
          self._setStatus("connected");
          // Force full allow-all resync so agent sees every open window/tab.
          return self.refreshTabs({ resync: true }).then(function () {
            return { ok: true, status: "connected", session_id: self.sessionId };
          });
        });
      });
    }).catch(function (e) {
      self._setStatus("error");
      return { ok: false, status: "error", error: String(e) };
    });
  };

  BrowserBridge.prototype._openWs = function () {
    var self = this;
    return new Promise(function (resolve, reject) {
      if (self._ws) {
        try { self._ws.close(); } catch (e) {}
        self._ws = null;
      }
      var proto = location.protocol === "https:" ? "wss:" : "ws:";
      var url = proto + "//" + location.host + "/api/browser/ws?session_id=" + encodeURIComponent(self.sessionId);
      var ws = new WebSocket(url);
      self._ws = ws;
      var opened = false;
      ws.onopen = function () {
        opened = true;
        self._startHeartbeat();
        resolve();
      };
      ws.onerror = function () {
        if (!opened) reject(new Error("websocket error"));
      };
      ws.onclose = function () {
        self._stopHeartbeat();
        self._active.clear();
        if (self.status === "connected") self._setStatus("disconnected");
      };
      ws.onmessage = function (ev) {
        var msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        self._onWsMessage(msg);
      };
    });
  };

  BrowserBridge.prototype._startHeartbeat = function () {
    var self = this;
    self._stopHeartbeat();
    self._heartbeatTimer = setInterval(function () {
      if (!self._ws || self._ws.readyState !== 1) return;
      self._ws.send(JSON.stringify({
        protocol: PROTOCOL,
        type: "browser.heartbeat",
        session_id: self.sessionId,
        timestamp: new Date().toISOString(),
        payload: {},
      }));
    }, HEARTBEAT_MS);
  };

  BrowserBridge.prototype._stopHeartbeat = function () {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  };

  BrowserBridge.prototype._onWsMessage = function (msg) {
    var self = this;
    var type = msg && msg.type;
    if (type === "browser.tool.execute") {
      self._handleToolExecute(msg);
      return;
    }
    if (type === "browser.tool.cancel") {
      var cid = msg.call_id || (msg.payload && msg.payload.call_id);
      if (cid) self._setActive(cid, "", {}, false);
      return;
    }
    if (type === "browser.hello") {
      self._setStatus("connected");
    }
  };

  BrowserBridge.prototype._handleToolExecute = function (msg) {
    var self = this;
    var payload = msg.payload || {};
    var callId = msg.call_id || payload.call_id;
    var tool = msg.tool || payload.tool;
    var args = msg.arguments || payload.arguments || {};

    self._setActive(callId, tool, args, true);

    self.sendToExtension({
      protocol: PROTOCOL,
      type: "TOOL_EXECUTE",
      session_id: self.sessionId,
      call_id: callId,
      tool: tool,
      arguments: args,
    }).then(function (response) {
      var result = (response && response.result) || response || {
        success: false,
        error: { code: "NO_RESPONSE", message: "Extension returned empty result" },
      };
      self._setActive(callId, tool, args, false);
      self._sendResult(callId, result);
      if (tool === "browser_tabs" || tool === "browser_navigate" || tool === "browser_click") {
        self.refreshTabs();
      }
    }).catch(function (err) {
      self._setActive(callId, tool, args, false);
      self._sendResult(callId, {
        success: false,
        error: {
          code: "EXTENSION_NOT_AVAILABLE",
          message: String(err && err.message ? err.message : err),
          recoverable: true,
        },
      });
    });
  };

  BrowserBridge.prototype._sendResult = function (callId, result) {
    if (!this._ws || this._ws.readyState !== 1) return;
    this._ws.send(JSON.stringify({
      protocol: PROTOCOL,
      type: "browser.tool.result",
      session_id: this.sessionId,
      call_id: callId,
      payload: { call_id: callId, result: result },
      result: result,
    }));
  };

  BrowserBridge.prototype.refreshTabs = function (opts) {
    var self = this;
    opts = opts || {};
    var msgType = opts.resync ? "RESYNC_TABS" : "LIST_TABS";
    return self.sendToExtension({
      protocol: PROTOCOL,
      type: msgType,
    }).then(function (response) {
      var tabs = (response && response.tabs) || [];
      self.tabs = tabs;
      self._lastTabMeta = {
        allow_all: !!(response && response.allow_all),
        open_count: response && response.open_count,
      };
      self._emit("tabs", tabs);
      self._renderChip();
      if (self._popoverOpen) self._renderPanel();
      if (self.sessionId && window.Tomo && Tomo.api) {
        Tomo.api("/api/browser/sessions/" + encodeURIComponent(self.sessionId) + "/tabs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tabs: tabs }),
        }).catch(function () {});
      }
      if (self._ws && self._ws.readyState === 1) {
        self._ws.send(JSON.stringify({
          protocol: PROTOCOL,
          type: "browser.tabs.updated",
          session_id: self.sessionId,
          payload: { tabs: tabs },
          tabs: tabs,
        }));
      }
      return tabs;
    }).catch(function () {
      return [];
    });
  };

  BrowserBridge.prototype.authorizeActiveTab = function () {
    var self = this;
    return self.sendToExtension({
      protocol: PROTOCOL,
      type: "AUTHORIZE_ACTIVE_TAB",
    }).then(function (response) {
      return self.refreshTabs().then(function () { return response; });
    });
  };

  BrowserBridge.prototype.disconnect = function () {
    var self = this;
    self._stopHeartbeat();
    self._active.clear();
    if (self._ws) {
      try { self._ws.close(); } catch (e) {}
      self._ws = null;
    }
    if (self.sessionId && window.Tomo && Tomo.api) {
      Tomo.api("/api/browser/sessions/" + encodeURIComponent(self.sessionId), {
        method: "DELETE",
      }).catch(function () {});
    }
    self.sessionId = null;
    self._setStatus("disconnected");
  };

  BrowserBridge.prototype.start = function (opts) {
    var self = this;
    opts = opts || {};
    self.bindUi();
    return Tomo.api("/api/browser/status").then(function (st) {
      if (st && st.extension_id) self.extensionId = st.extension_id;
      if (opts.extensionId) self.extensionId = opts.extensionId;
      if (!self.hasChromeRuntime()) {
        self._setStatus("unsupported");
        return { status: self.status };
      }
      return self.ping().then(function (pong) {
        if (pong.ok && opts.autoConnect !== false) {
          return self.connect();
        }
        return pong;
      });
    }).catch(function () {
      if (self.extensionId) return self.ping();
      self._setStatus("not_installed");
      return { status: self.status };
    });
  };

  // ── Composer chip + popover (no floating HUD) ─────────────────────

  BrowserBridge.prototype.bindUi = function () {
    var self = this;
    this._chip = document.getElementById("browserStatusChip");
    this._panel = document.getElementById("browserControlPanel");
    if (this._chip && !this._chip.dataset.tomoBound) {
      this._chip.dataset.tomoBound = "1";
      this._chip.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        self.togglePopover();
      });
    }
    if (!document.documentElement.dataset.tomoBrowserDocBound) {
      document.documentElement.dataset.tomoBrowserDocBound = "1";
      document.addEventListener("click", function (e) {
        if (!self._popoverOpen) return;
        var wrap = document.getElementById("browserStatusWrap");
        if (wrap && wrap.contains(e.target)) return;
        self.closePopover();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && self._popoverOpen) self.closePopover();
      });
    }
    this._renderChip();
  };

  BrowserBridge.prototype.togglePopover = function () {
    if (this._popoverOpen) this.closePopover();
    else this.openPopover();
  };

  BrowserBridge.prototype.openPopover = function () {
    this._popoverOpen = true;
    if (this._chip) this._chip.setAttribute("aria-expanded", "true");
    if (this._panel) {
      this._panel.hidden = false;
      this._panel.classList.remove("hidden");
    }
    this._renderPanel();
  };

  BrowserBridge.prototype.closePopover = function () {
    this._popoverOpen = false;
    if (this._chip) this._chip.setAttribute("aria-expanded", "false");
    if (this._panel) {
      this._panel.hidden = true;
      this._panel.classList.add("hidden");
    }
  };

  BrowserBridge.prototype._renderChip = function () {
    var chip = this._chip || document.getElementById("browserStatusChip");
    if (!chip) return;
    this._chip = chip;
    var controlling = this.isControlling();
    var status = this.status;
    var label, cls;
    if (controlling) {
      label = this.currentActivity() || "Controlling";
      cls = "browser-status-chip is-live";
    } else if (status === "connected") {
      var n = (this.tabs || []).length;
      label = n ? ("Chrome · " + n + " tab" + (n === 1 ? "" : "s")) : "Chrome · Connected";
      cls = "browser-status-chip is-on";
    } else if (status === "connecting") {
      label = "Chrome · Connecting";
      cls = "browser-status-chip is-warn";
    } else if (status === "installed") {
      label = "Chrome · Ready";
      cls = "browser-status-chip is-warn";
    } else if (status === "not_installed" || status === "unsupported") {
      label = "Browser · Off";
      cls = "browser-status-chip is-off";
    } else {
      label = "Browser · " + status;
      cls = "browser-status-chip is-off";
    }
    chip.className = cls;
    chip.innerHTML =
      '<span class="browser-status-dot" aria-hidden="true"></span>' +
      '<span class="browser-status-label">' + Tomo.escapeHtml(label) + "</span>";
  };

  BrowserBridge.prototype._renderPanel = function () {
    var el = this._panel || document.getElementById("browserControlPanel");
    if (!el) return;
    this._panel = el;
    var self = this;
    var status = this.status;
    var tabs = this.tabs || [];
    var controlling = this.isControlling();
    var activity = this.currentActivity();
    var caps = this.capabilities || [];

    var dot =
      controlling ? "live" :
      status === "connected" ? "ok" :
      status === "connecting" || status === "installed" ? "warn" : "err";
    var label =
      controlling ? ("Controlling · " + (activity || "Working…")) :
      status === "connected" ? "Connected — ready" :
      status === "connecting" ? "Connecting…" :
      status === "installed" ? "Extension found (not linked)" :
      status === "not_installed" ? "Extension not installed" :
      status === "unsupported" ? "Unsupported browser" :
      status === "disconnected" ? "Disconnected" : status;

    var liveBanner = controlling
      ? '<div class="browser-live-banner" role="status">' +
          '<span class="browser-live-pulse"></span>' +
          '<div><strong>Tomo is controlling your browser</strong>' +
          '<div class="browser-muted">' + Tomo.escapeHtml(activity || "Working…") + "</div></div></div>"
      : "";

    var tabsHtml = "";
    if (tabs.length) {
      tabsHtml = '<ul class="browser-tab-list">' + tabs.slice(0, 8).map(function (t) {
        return '<li><span class="browser-tab-title">' + Tomo.escapeHtml(t.title || t.url || t.id) +
          '</span><span class="browser-tab-url">' + Tomo.escapeHtml(t.domain || t.url || "") + "</span></li>";
      }).join("") + "</ul>";
      if (tabs.length > 8) {
        tabsHtml += '<p class="browser-muted">+' + (tabs.length - 8) + " more</p>";
      }
    } else if (status === "connected") {
      tabsHtml = '<p class="browser-muted">No tabs yet. Enable “Control all tabs” in the extension popup.</p>';
    }

    var actions = "";
    if (status === "not_installed" || status === "unsupported") {
      actions =
        '<p class="browser-muted">Load the unpacked extension from <code>extension/</code> and set <code>TOMO_BROWSER_EXTENSION_ID</code>.</p>' +
        '<button type="button" class="btn ghost sm" data-browser-act="retry">Retry</button>';
    } else if (status === "disconnected" || status === "installed" || status === "error") {
      actions = '<button type="button" class="btn primary sm" data-browser-act="connect">Connect</button>';
    } else if (status === "connected") {
      actions =
        '<button type="button" class="btn primary sm" data-browser-act="resync">Resync all tabs</button>' +
        '<button type="button" class="btn ghost sm" data-browser-act="add-tab">Add tab</button>' +
        '<button type="button" class="btn ghost sm" data-browser-act="refresh">Refresh</button>' +
        '<button type="button" class="btn ghost sm" data-browser-act="disconnect">Disconnect</button>';
    }

    el.innerHTML =
      '<div class="browser-panel-inner' + (controlling ? " is-controlling" : "") + '">' +
      liveBanner +
      '<div class="browser-panel-head"><span class="browser-dot ' + dot + '"></span> ' +
      Tomo.escapeHtml(label) + "</div>" +
      (status === "connected"
        ? '<div class="browser-section-label">Tabs (' + tabs.length + ")</div>" + tabsHtml
        : "") +
      '<div class="browser-actions">' + actions + "</div>" +
      "</div>";

    el.querySelectorAll("[data-browser-act]").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute("data-browser-act");
        if (act === "retry" || act === "connect") self.start({ autoConnect: true });
        else if (act === "resync") {
          self.refreshTabs({ resync: true }).then(function (tabs) {
            if (Tomo.toast) {
              Tomo.toast("Resynced " + (tabs ? tabs.length : 0) + " tabs", "ok");
            }
          });
        } else if (act === "add-tab") {
          self.authorizeActiveTab().then(function () {
            if (Tomo.toast) Tomo.toast("Tab authorized", "ok");
          });
        } else if (act === "refresh") self.refreshTabs();
        else if (act === "disconnect") self.disconnect();
      });
    });
  };

  /** Keep chat-scroll padding in sync with absolute .chat-dock height. */
  BrowserBridge.prototype.syncDockHeight = function () {
    var main = document.querySelector(".sessions-chat .chat-main");
    var dock = document.getElementById("chatDock");
    if (!main || !dock) return;
    var h = Math.ceil(dock.getBoundingClientRect().height || 0);
    if (h < 100) h = 140;
    main.style.setProperty("--chat-dock-h", h + "px");
  };

  // Back-compat no-ops (HUD removed)
  BrowserBridge.prototype.ensureHud = function () { return null; };
  BrowserBridge.prototype.mountPanel = function () {
    this.bindUi();
  };

  function boot() {
    if (!window.Tomo) return;
    var bridge = new BrowserBridge();
    Tomo.browser = bridge;
    bridge.bindUi();

    var hasChip = !!document.getElementById("browserStatusChip");
    var onSessions = !!document.querySelector(".sessions-page");
    if (hasChip || onSessions || document.querySelector(".page-chat-home")) {
      bridge.start({ autoConnect: true }).catch(function () {});
    }

    bridge.syncDockHeight();
    window.addEventListener("resize", function () { bridge.syncDockHeight(); });
    bridge.on("status", function () { requestAnimationFrame(function () { bridge.syncDockHeight(); }); });
    bridge.on("activity", function () { requestAnimationFrame(function () { bridge.syncDockHeight(); }); });
    setTimeout(function () { bridge.syncDockHeight(); }, 80);
    setTimeout(function () { bridge.syncDockHeight(); }, 400);
    if (typeof ResizeObserver !== "undefined") {
      var dock = document.getElementById("chatDock");
      if (dock) {
        var ro = new ResizeObserver(function () { bridge.syncDockHeight(); });
        ro.observe(dock);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

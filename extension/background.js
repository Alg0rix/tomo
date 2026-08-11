/**
 * Tomo Browser extension — service worker.
 *
 * Handles:
 * - external messages from Tomo web (externally_connectable)
 * - internal messages from the popup
 * - tool execution via chrome.debugger
 */

import { CAPABILITIES, executeTool } from "./lib/executor.js";
import * as tabs from "./lib/tabs.js";

const PROTOCOL = "tomo.browser.v1";
const VERSION = "0.1.6";

// Ensure Control-all-tabs default is durable + seed open tabs on SW start.
tabs.bootstrapAllowAll().catch(() => {});

chrome.runtime.onInstalled.addListener(() => {
  tabs.bootstrapAllowAll().catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  tabs.bootstrapAllowAll().catch(() => {});
});

function pong(nonce) {
  return {
    protocol: PROTOCOL,
    type: "TOMO_PONG",
    nonce,
    extension: { version: VERSION },
    browser: { name: "chrome" },
    capabilities: CAPABILITIES,
  };
}

function isTomoOrigin(url) {
  if (!url) return false;
  try {
    const u = new URL(url);
    if (u.hostname === "localhost" || u.hostname === "127.0.0.1") return true;
    if (u.hostname === "app.tomo.dev" || u.hostname === "staging.tomo.dev") return true;
    // Local LAN / custom hosts: allow http(s) when externally_connectable matched.
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

async function handleExternal(message, sender) {
  if (!message || message.protocol !== PROTOCOL) {
    return { success: false, error: { code: "BAD_PROTOCOL", message: "unsupported protocol" } };
  }
  if (sender?.url && !isTomoOrigin(sender.url)) {
    return { success: false, error: { code: "PERMISSION_DENIED", message: "origin not allowed" } };
  }

  switch (message.type) {
    case "TOMO_PING":
      try {
        await chrome.action.setBadgeText({ text: "ON" });
        await chrome.action.setBadgeBackgroundColor({ color: "#3ecf8e" });
        await chrome.action.setTitle({ title: "Tomo Browser — ready" });
      } catch {
        /* ignore */
      }
      return pong(message.nonce);
    case "LIST_TABS": {
      // Always re-seed when allow-all so agent/frontend see every open tab.
      if (await tabs.getAllowAll()) {
        await tabs.authorizeAllTabs();
      }
      const list = await tabs.listAuthorizedTabs();
      return {
        success: true,
        allow_all: await tabs.getAllowAll(),
        open_count: (await tabs.queryAllTabs()).length,
        tabs: list,
      };
    }
    case "RESYNC_TABS": {
      await tabs.setAllowAll(true);
      const list = await tabs.authorizeAllTabs();
      return {
        success: true,
        allow_all: true,
        open_count: (await tabs.queryAllTabs()).length,
        tabs: list,
      };
    }
    case "AUTHORIZE_ACTIVE_TAB": {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!active?.id) {
        return {
          success: false,
          error: { code: "TAB_NOT_FOUND", message: "No active tab" },
        };
      }
      try {
        const tab = await tabs.authorizeChromeTab(active.id);
        return { success: true, tab };
      } catch (e) {
        return {
          success: false,
          error: { code: "PERMISSION_DENIED", message: e.message || String(e) },
        };
      }
    }
    case "TOOL_EXECUTE": {
      const tool = message.tool;
      const args = message.arguments || {};
      try {
        // Badge: show that Tomo is actively controlling this browser.
        try {
          await chrome.action.setBadgeBackgroundColor({ color: "#3d8bfd" });
          await chrome.action.setBadgeText({ text: "…" });
          await chrome.action.setTitle({
            title: `Tomo Browser — controlling (${tool || "tool"})`,
          });
        } catch {
          /* ignore badge failures */
        }
        const result = await executeTool(tool, args);
        try {
          await chrome.action.setBadgeText({ text: "ON" });
          await chrome.action.setBadgeBackgroundColor({ color: "#3ecf8e" });
          await chrome.action.setTitle({
            title: "Tomo Browser — connected / ready",
          });
        } catch {
          /* ignore */
        }
        return {
          protocol: PROTOCOL,
          type: "TOOL_RESULT",
          call_id: message.call_id,
          result,
        };
      } catch (e) {
        try {
          await chrome.action.setBadgeText({ text: "!" });
          await chrome.action.setBadgeBackgroundColor({ color: "#e06c75" });
        } catch {
          /* ignore */
        }
        return {
          protocol: PROTOCOL,
          type: "TOOL_RESULT",
          call_id: message.call_id,
          result: {
            success: false,
            error: {
              code: e.code || "ERROR",
              message: e.message || String(e),
              recoverable: !!e.recoverable,
            },
          },
        };
      }
    }
    default:
      return {
        success: false,
        error: { code: "UNKNOWN_TYPE", message: `Unknown message type: ${message.type}` },
      };
  }
}

// Page → extension (externally_connectable)
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  handleExternal(message, sender).then(sendResponse).catch((e) => {
    sendResponse({
      success: false,
      error: { code: "ERROR", message: e.message || String(e) },
    });
  });
  return true; // async
});

// Popup → extension
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (!message || typeof message !== "object") return;
    if (message.type === "POPUP_LIST_TABS") {
      if (await tabs.getAllowAll()) {
        await tabs.authorizeAllTabs();
      }
      const list = await tabs.listAllTabsForPopup();
      sendResponse({
        tabs: list,
        allowAll: await tabs.getAllowAll(),
        openCount: (await tabs.queryAllTabs()).length,
      });
      return;
    }
    if (message.type === "POPUP_RESYNC_ALL") {
      await tabs.setAllowAll(true);
      const list = await tabs.authorizeAllTabs();
      sendResponse({
        ok: true,
        allowAll: true,
        tabs: list,
        openCount: (await tabs.queryAllTabs()).length,
      });
      return;
    }
    if (message.type === "POPUP_SET_ALLOW_ALL") {
      // Popup already writes chrome.storage.local; re-apply for SW memory paths.
      const enabled = await tabs.setAllowAll(!!message.enabled);
      if (enabled) {
        await tabs.authorizeAllTabs();
      }
      sendResponse({ ok: true, allowAll: enabled });
      return;
    }
    if (message.type === "POPUP_AUTHORIZE") {
      const tab = await tabs.authorizeChromeTab(message.chromeTabId);
      sendResponse({ ok: true, tab });
      return;
    }
    if (message.type === "POPUP_REVOKE") {
      await tabs.revokeChromeTab(message.chromeTabId);
      sendResponse({ ok: true });
      return;
    }
    if (message.type === "POPUP_PING") {
      sendResponse(pong(message.nonce));
    }
  })().catch((e) => sendResponse({ ok: false, error: e.message || String(e) }));
  return true;
});

console.info("[Tomo Browser] service worker ready", VERSION);

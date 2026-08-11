/** Map tool names → driver actions. */

import * as tabs from "./tabs.js";
import * as snapshot from "./snapshot.js";
import * as actions from "./actions.js";
import * as cdp from "./debugger.js";
import { hideControlOverlay, showControlOverlay } from "./overlay.js";

function err(code, message, extra = {}) {
  return {
    success: false,
    error: { code, message, recoverable: !!extra.recoverable, ...extra },
  };
}

function wrapError(e) {
  return err(e.code || "ERROR", e.message || String(e), {
    recoverable: !!e.recoverable,
    suggested_action: e.suggested_action,
  });
}

async function withTab(virtualId, tool, fn) {
  let chromeId = null;
  try {
    chromeId = await tabs.requireAuthorizedVirtual(virtualId);
    await showControlOverlay(chromeId, tool);
    return await fn(chromeId);
  } catch (e) {
    return wrapError(e);
  } finally {
    if (chromeId != null) {
      await hideControlOverlay(chromeId);
    }
  }
}

export async function executeTool(tool, args = {}) {
  const a = args || {};
  switch (tool) {
    case "browser_tabs": {
      // Live enumeration across all windows (allow-all or explicit grants).
      try {
        // Re-assert allow-all seed so a stuck empty grant set cannot hide tabs.
        if (await tabs.getAllowAll()) {
          await tabs.authorizeAllTabs();
        }
        const list = await tabs.listAuthorizedTabs();
        const open = await tabs.queryAllTabs();
        const [active] = await chrome.tabs.query({
          active: true,
          currentWindow: true,
        });
        if (active?.id) {
          await showControlOverlay(active.id, tool);
          setTimeout(() => hideControlOverlay(active.id), 600);
        }
        return {
          success: true,
          allow_all: await tabs.getAllowAll(),
          open_count: open.length,
          tabs: list,
        };
      } catch (e) {
        return wrapError(e);
      }
    }
    case "browser_attach":
      return withTab(a.tab_id, tool, async (cid) => {
        await cdp.attach(cid);
        const t = await chrome.tabs.get(cid);
        return {
          success: true,
          tab: { id: a.tab_id, title: t.title || "", url: t.url || "" },
        };
      });
    case "browser_snapshot":
      return withTab(a.tab_id, tool, async (cid) => {
        const res = await snapshot.captureSnapshot(cid);
        if (res.tab) res.tab.id = a.tab_id;
        return res;
      });
    case "browser_click":
      return withTab(a.tab_id, tool, (cid) =>
        actions.click(cid, a.ref, a.snapshot_version)
      );
    case "browser_type":
      return withTab(a.tab_id, tool, (cid) =>
        actions.typeText(cid, a.ref, a.text, !!a.submit, a.snapshot_version)
      );
    case "browser_press":
      return withTab(a.tab_id, tool, (cid) =>
        actions.press(cid, a.key, a.ref, a.snapshot_version)
      );
    case "browser_select":
      return withTab(a.tab_id, tool, (cid) =>
        actions.selectOption(cid, a.ref, a.value, a.snapshot_version)
      );
    case "browser_scroll":
      return withTab(a.tab_id, tool, (cid) =>
        actions.scroll(cid, a.direction, a.amount, a.ref, a.snapshot_version)
      );
    case "browser_navigate":
      return withTab(a.tab_id, tool, (cid) => actions.navigate(cid, a.url));
    case "browser_back":
      return withTab(a.tab_id, tool, (cid) => actions.historyGo(cid, -1));
    case "browser_forward":
      return withTab(a.tab_id, tool, (cid) => actions.historyGo(cid, 1));
    case "browser_wait":
      return withTab(a.tab_id, tool, (cid) => actions.wait(cid, a.ms));
    case "browser_screenshot":
      return withTab(a.tab_id, tool, async (cid) => {
        const res = await actions.screenshot(cid);
        if (res.page) res.tab = { id: a.tab_id, ...res.page };
        return res;
      });
    case "browser_extract":
      return withTab(a.tab_id, tool, async (cid) => {
        const text = await snapshot.extractText(cid, a.ref, a.snapshot_version);
        return { success: true, text };
      });
    default:
      return err("CAPABILITY_NOT_SUPPORTED", `Unknown tool: ${tool}`);
  }
}

export const CAPABILITIES = [
  "browser.tabs",
  "browser.attach",
  "browser.snapshot",
  "browser.click",
  "browser.type",
  "browser.press",
  "browser.select",
  "browser.scroll",
  "browser.navigate",
  "browser.back",
  "browser.forward",
  "browser.wait",
  "browser.screenshot",
  "browser.extract",
];

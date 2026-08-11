/** Show / hide the in-page control wave overlay on a Chrome tab. */

const ACTIVITY = {
  browser_tabs: "Listing tabs",
  browser_attach: "Attaching",
  browser_snapshot: "Reading page",
  browser_click: "Clicking",
  browser_type: "Typing",
  browser_press: "Key press",
  browser_select: "Selecting",
  browser_scroll: "Scrolling",
  browser_navigate: "Navigating",
  browser_back: "Going back",
  browser_forward: "Going forward",
  browser_wait: "Waiting",
  browser_screenshot: "Screenshot",
  browser_extract: "Extracting text",
};

export function activityLabel(tool) {
  return ACTIVITY[tool] || "Controlling";
}

async function ensureContentScript(tabId) {
  // Prefer messaging an already-injected content script.
  try {
    const res = await chrome.tabs.sendMessage(tabId, { type: "TOMO_CONTROL_PING" });
    if (res && res.ok) return true;
  } catch {
    // not injected yet
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content/control-overlay.js"],
    });
    return true;
  } catch (e) {
    // Privileged pages / chrome:// / PDF viewer may refuse injection.
    console.debug("[Tomo] overlay inject failed", tabId, e);
    return false;
  }
}

export async function showControlOverlay(tabId, tool) {
  if (tabId == null) return;
  const ok = await ensureContentScript(tabId);
  if (!ok) return;
  try {
    await chrome.tabs.sendMessage(tabId, {
      type: "TOMO_CONTROL_START",
      label: activityLabel(tool),
      tool: tool || "",
    });
  } catch (e) {
    console.debug("[Tomo] overlay show failed", e);
  }
}

export async function hideControlOverlay(tabId, { force = false } = {}) {
  if (tabId == null) return;
  try {
    await chrome.tabs.sendMessage(tabId, {
      type: "TOMO_CONTROL_END",
      force: !!force,
    });
  } catch {
    // tab gone or no content script — fine
  }
}

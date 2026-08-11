/** chrome.debugger CDP transport. */

const PROTOCOL_VERSION = "1.3";
const attached = new Set();

export async function attach(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, PROTOCOL_VERSION);
  } catch (e) {
    // Already attached by us or another client.
    const msg = String(e && e.message ? e.message : e);
    if (!/already attached/i.test(msg)) {
      const err = new Error(`Attach failed: ${msg}`);
      err.code = "ATTACH_FAILED";
      throw err;
    }
  }
  attached.add(tabId);
  try {
    await send(tabId, "DOM.enable");
    await send(tabId, "Runtime.enable");
    await send(tabId, "Page.enable");
    await send(tabId, "Accessibility.enable").catch(() => {});
  } catch {
    // domains optional
  }
}

export async function detach(tabId) {
  if (!attached.has(tabId)) return;
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // ignore
  }
  attached.delete(tabId);
}

export async function send(tabId, method, params = {}) {
  await attach(tabId);
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId != null) attached.delete(source.tabId);
});

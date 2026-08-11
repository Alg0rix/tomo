/**
 * Popup UI — Control all tabs persists in chrome.storage.local so closing
 * the popup (or killing the service worker) cannot reset the preference.
 */
const MODE_KEY = "tomo_allow_all";

const listEl = document.getElementById("list");
const listHint = document.getElementById("listHint");
const refreshBtn = document.getElementById("refresh");
const resyncBtn = document.getElementById("resyncAll");
const authActiveBtn = document.getElementById("authActive");
const allowAllEl = document.getElementById("allowAll");

let ignoreAllowChange = false;
let loadGen = 0;

async function readAllowAllFromStorage() {
  try {
    const data = await chrome.storage.local.get(MODE_KEY);
    if (!(MODE_KEY in data) || data[MODE_KEY] === undefined || data[MODE_KEY] === null) {
      return true; // default on
    }
    return data[MODE_KEY] === true || data[MODE_KEY] === 1 || data[MODE_KEY] === "true";
  } catch {
    return true;
  }
}

async function writeAllowAllToStorage(enabled) {
  const value = !!enabled;
  await chrome.storage.local.set({
    [MODE_KEY]: value,
    tomo_allow_all_updated_at: Date.now(),
  });
  // Notify service worker (best-effort; storage is already durable).
  try {
    await chrome.runtime.sendMessage({
      type: "POPUP_SET_ALLOW_ALL",
      enabled: value,
    });
  } catch {
    /* SW may be restarting — local write already done */
  }
  return value;
}

function setAllowCheckbox(on) {
  ignoreAllowChange = true;
  allowAllEl.checked = !!on;
  // defer clear so any synthetic events from setting .checked are ignored
  Promise.resolve().then(() => {
    ignoreAllowChange = false;
  });
}

function applyAllowUi(allowAll) {
  setAllowCheckbox(allowAll);
  listHint.textContent = allowAll
    ? "All normal tabs are allowed. Uncheck “Control all tabs” to pick individually."
    : "Only checked tabs are allowed.";
  authActiveBtn.disabled = allowAll;
  authActiveBtn.textContent = allowAll ? "All tabs allowed" : "Allow this tab only";
}

async function load() {
  const gen = ++loadGen;
  listEl.textContent = "Loading…";

  // 1) Prefer durable storage so UI never flashes "off" if SW is slow.
  let allowAll = await readAllowAllFromStorage();
  if (gen !== loadGen) return;
  applyAllowUi(allowAll);

  // 2) Tab list via service worker (also returns allowAll for consistency).
  let tabs = [];
  let openCount = 0;
  try {
    const res = await chrome.runtime.sendMessage({ type: "POPUP_LIST_TABS" });
    if (gen !== loadGen) return;
    if (res && typeof res.allowAll === "boolean") {
      allowAll = res.allowAll;
      applyAllowUi(allowAll);
    }
    tabs = (res && res.tabs) || [];
    openCount = Number(res && res.openCount) || tabs.length;
    listHint.textContent = allowAll
      ? `Control all tabs ON · showing ${tabs.length} controllable of ${openCount} open`
      : `Per-tab mode · ${tabs.filter((t) => t.authorized).length} authorized of ${tabs.length}`;
  } catch (e) {
    listHint.textContent =
      (listHint.textContent || "") +
      " (Could not refresh tab list — preference still saved.)";
  }

  if (!tabs.length) {
    listEl.innerHTML =
      '<div class="empty">No controllable tabs found. Open some http(s) pages, then click “Resync all tabs”.</div>';
    return;
  }

  listEl.innerHTML = "";
  for (const t of tabs) {
    const row = document.createElement("label");
    row.className = "row" + (t.active ? " active" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = allowAll || !!t.authorized;
    cb.disabled = allowAll;
    if (!allowAll) {
      cb.addEventListener("change", async () => {
        try {
          if (cb.checked) {
            await chrome.runtime.sendMessage({
              type: "POPUP_AUTHORIZE",
              chromeTabId: t.chromeTabId,
            });
          } else {
            await chrome.runtime.sendMessage({
              type: "POPUP_REVOKE",
              chromeTabId: t.chromeTabId,
            });
          }
        } catch {
          /* ignore */
        }
        load();
      });
    }
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<div class="title"></div><div class="url"></div>`;
    meta.querySelector(".title").textContent = t.title || "(untitled)";
    meta.querySelector(".url").textContent = t.domain || t.url || "";
    row.appendChild(cb);
    row.appendChild(meta);
    listEl.appendChild(row);
  }
}

allowAllEl.addEventListener("change", async () => {
  if (ignoreAllowChange) return;
  const enabled = allowAllEl.checked;
  // Optimistically update UI, then persist before anything else.
  applyAllowUi(enabled);
  listHint.textContent = enabled ? "Saving…" : "Saving…";
  try {
    await writeAllowAllToStorage(enabled);
    listHint.textContent = enabled
      ? "All normal tabs are allowed. Preference saved."
      : "Only checked tabs are allowed. Preference saved.";
  } catch (e) {
    listHint.textContent = "Could not save preference — try again.";
    // Re-read truth from storage
    applyAllowUi(await readAllowAllFromStorage());
  }
  load();
});

refreshBtn.addEventListener("click", load);
if (resyncBtn) {
  resyncBtn.addEventListener("click", async () => {
    listHint.textContent = "Resyncing all windows…";
    try {
      await chrome.storage.local.set({
        [MODE_KEY]: true,
        tomo_allow_all_updated_at: Date.now(),
      });
      const res = await chrome.runtime.sendMessage({ type: "POPUP_RESYNC_ALL" });
      applyAllowUi(true);
      listHint.textContent = res && res.ok
        ? `Resynced · ${((res.tabs || []).length)} tabs granted (open=${res.openCount || "?"})`
        : "Resync finished — reloading list…";
    } catch (e) {
      listHint.textContent = "Resync failed: " + (e && e.message ? e.message : e);
    }
    load();
  });
}
authActiveBtn.addEventListener("click", async () => {
  if (allowAllEl.checked) return;
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!active?.id) return;
  try {
    await chrome.runtime.sendMessage({
      type: "POPUP_AUTHORIZE",
      chromeTabId: active.id,
    });
  } catch {
    /* ignore */
  }
  load();
});

// Keep checkbox in sync if another context changes storage.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[MODE_KEY]) return;
  const v = changes[MODE_KEY].newValue;
  const on =
    v === undefined || v === null
      ? true
      : v === true || v === 1 || v === "true";
  applyAllowUi(on);
});

// Flush: if user toggled and immediately closed, change handler already awaited
// writeAllowAllToStorage. Also re-assert current checkbox on pagehide.
window.addEventListener("pagehide", () => {
  // Best-effort sync without awaiting (page is closing).
  try {
    chrome.storage.local.set({
      [MODE_KEY]: !!allowAllEl.checked,
      tomo_allow_all_updated_at: Date.now(),
    });
  } catch {
    /* ignore */
  }
});

// Initial paint from storage before network round-trip.
readAllowAllFromStorage().then((on) => {
  applyAllowUi(on);
  load();
});

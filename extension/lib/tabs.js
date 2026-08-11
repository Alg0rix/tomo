/** Virtual tab id map + authorization state.

Two modes:
* **allowAll** (default true) — every non-privileged tab is authorized
* **selected** — only explicitly checked tabs

Persistence (survives popup close + extension SW restart):
* ``tomo_allow_all`` → chrome.storage.local
* ``tomo_authorized`` → chrome.storage.local (per-tab chrome ids when not allow-all)
* ``tomo_tab_map`` → chrome.storage.session (virtual ↔ chrome ids)
*/

import { domainOf, isBlockedUrl, newId } from "./ids.js";

const AUTH_KEY = "tomo_authorized";
const MODE_KEY = "tomo_allow_all";
const MAP_KEY = "tomo_tab_map";
const DEBUG_KEY = "tomo_tabs_debug";

/** @type {Map<string, number>} virtualTabId → chrome tabId */
const virtualToChrome = new Map();
/** @type {Map<number, string>} chrome tabId → virtualTabId */
const chromeToVirtual = new Map();

let mapHydrated = false;

async function loadAuthSet() {
  try {
    const local = await chrome.storage.local.get(AUTH_KEY);
    let arr = local[AUTH_KEY];
    if (!Array.isArray(arr)) {
      try {
        const sess = await chrome.storage.session.get(AUTH_KEY);
        arr = sess[AUTH_KEY];
        if (Array.isArray(arr)) {
          await chrome.storage.local.set({ [AUTH_KEY]: arr });
        }
      } catch {
        /* ignore */
      }
    }
    return new Set(
      Array.isArray(arr) ? arr.map(Number).filter((n) => Number.isFinite(n)) : []
    );
  } catch {
    return new Set();
  }
}

async function saveAuthSet(set) {
  const arr = [...set];
  try {
    await chrome.storage.local.set({ [AUTH_KEY]: arr });
  } catch {
    /* ignore */
  }
  try {
    await chrome.storage.session.set({ [AUTH_KEY]: arr });
  } catch {
    /* ignore */
  }
}

/** Default: control all tabs (true when key never written). */
export async function getAllowAll() {
  try {
    const data = await chrome.storage.local.get(MODE_KEY);
    if (!(MODE_KEY in data) || data[MODE_KEY] === undefined || data[MODE_KEY] === null) {
      return true;
    }
    // Accept boolean true / 1 / "true" / "1"
    const v = data[MODE_KEY];
    if (v === true || v === 1 || v === "true" || v === "1") return true;
    if (v === false || v === 0 || v === "false" || v === "0") return false;
    // Unknown shape → prefer allow-all (safer for "I enabled it" UX)
    return true;
  } catch {
    return true;
  }
}

export async function setAllowAll(enabled) {
  const value = !!enabled;
  await chrome.storage.local.set({
    [MODE_KEY]: value,
    tomo_allow_all_updated_at: Date.now(),
  });
  try {
    await chrome.storage.session.set({ [MODE_KEY]: value });
  } catch {
    /* ignore */
  }
  if (value) {
    // Snapshot every open controllable tab into the auth set as a belt-and-suspenders
    // backup (list still prefers live query when allowAll is on).
    await seedAuthSetFromOpenTabs();
  }
  return value;
}

/**
 * Enumerate tabs across ALL normal browser windows.
 * Prefer windows.getAll({populate:true}) — more reliable than tabs.query({}) alone
 * in some MV3 service-worker / multi-window situations.
 */
export async function queryAllTabs() {
  const byId = new Map();

  try {
    const windows = await chrome.windows.getAll({
      populate: true,
      windowTypes: ["normal", "popup"],
    });
    for (const w of windows || []) {
      for (const t of w.tabs || []) {
        if (t?.id != null) byId.set(t.id, t);
      }
    }
  } catch (e) {
    console.warn("[Tomo] windows.getAll failed", e);
  }

  // Always merge tabs.query as a second source.
  try {
    const q = await chrome.tabs.query({});
    for (const t of q || []) {
      if (t?.id != null) byId.set(t.id, t);
    }
  } catch (e) {
    console.warn("[Tomo] tabs.query failed", e);
  }

  return [...byId.values()];
}

async function seedAuthSetFromOpenTabs() {
  const all = await queryAllTabs();
  const set = new Set();
  for (const t of all) {
    if (isControllable(t)) set.add(t.id);
  }
  await saveAuthSet(set);
  return set.size;
}

async function writeDebug(info) {
  try {
    await chrome.storage.local.set({
      [DEBUG_KEY]: { ...info, at: Date.now() },
    });
  } catch {
    /* ignore */
  }
}

async function hydrateMaps() {
  if (mapHydrated) return;
  mapHydrated = true;
  try {
    const data = await chrome.storage.session.get(MAP_KEY);
    const raw = data[MAP_KEY];
    if (!raw || typeof raw !== "object") return;
    for (const [vid, cid] of Object.entries(raw)) {
      const n = Number(cid);
      if (!vid || !Number.isFinite(n)) continue;
      virtualToChrome.set(vid, n);
      chromeToVirtual.set(n, vid);
    }
  } catch {
    /* ignore */
  }
}

async function persistMaps() {
  const obj = {};
  for (const [vid, cid] of virtualToChrome.entries()) {
    obj[vid] = cid;
  }
  try {
    await chrome.storage.session.set({ [MAP_KEY]: obj });
  } catch {
    /* ignore */
  }
}

export function resolveChromeTabId(virtualId) {
  return virtualToChrome.get(virtualId) ?? null;
}

export function resolveVirtualId(chromeTabId) {
  return chromeToVirtual.get(chromeTabId) ?? null;
}

export function ensureVirtualId(chromeTabId) {
  let vid = chromeToVirtual.get(chromeTabId);
  if (vid) return vid;
  // Stable per Chrome tab for this browser session so re-lists don't churn ids.
  // Prefixed so agents never treat it as a raw CDP/tab handle.
  vid = `tab_c${chromeTabId}`;
  chromeToVirtual.set(chromeTabId, vid);
  virtualToChrome.set(vid, chromeTabId);
  persistMaps();
  return vid;
}

function tabPublic(tab, authorized) {
  const vid = ensureVirtualId(tab.id);
  const url = tab.url || tab.pendingUrl || "";
  return {
    id: vid,
    title: tab.title || "",
    url,
    domain: domainOf(url),
    authorized: !!authorized,
    windowId: tab.windowId,
    active: !!tab.active,
  };
}

/**
 * Controllable = real browser tab we can attach the debugger to.
 * Empty/pending URL still allowed (page loading). Privileged schemes blocked.
 */
export function isControllable(tab) {
  if (tab?.id == null) return false;
  const url = String(tab.url || tab.pendingUrl || "");
  // No URL yet (loading) — still list it; attach may wait.
  if (!url) return true;
  return !isBlockedUrl(url);
}

export async function isAuthorized(chromeTabId) {
  await hydrateMaps();
  if (await getAllowAll()) {
    try {
      const tab = await chrome.tabs.get(chromeTabId);
      return isControllable(tab);
    } catch {
      return false;
    }
  }
  const set = await loadAuthSet();
  return set.has(chromeTabId);
}

export async function authorizeChromeTab(chromeTabId) {
  await hydrateMaps();
  const tab = await chrome.tabs.get(chromeTabId);
  const url = tab.url || tab.pendingUrl || "";
  if (url && isBlockedUrl(url)) {
    throw new Error("Cannot authorize privileged Chrome pages");
  }
  const set = await loadAuthSet();
  set.add(chromeTabId);
  await saveAuthSet(set);
  return tabPublic(tab, true);
}

export async function revokeChromeTab(chromeTabId) {
  await hydrateMaps();
  if (await getAllowAll()) {
    await setAllowAll(false);
    const all = await queryAllTabs();
    const set = new Set();
    for (const t of all) {
      if (isControllable(t) && t.id !== chromeTabId) set.add(t.id);
    }
    await saveAuthSet(set);
    return;
  }
  const set = await loadAuthSet();
  set.delete(chromeTabId);
  await saveAuthSet(set);
}

export async function authorizeAllTabs() {
  await setAllowAll(true);
  const n = await seedAuthSetFromOpenTabs();
  const list = await listAuthorizedTabs();
  await writeDebug({
    event: "authorizeAllTabs",
    seeded: n,
    listed: list.length,
    allowAll: true,
  });
  return list;
}

export async function listAuthorizedTabs() {
  await hydrateMaps();
  const allowAll = await getAllowAll();
  const all = await queryAllTabs();
  const controllable = all.filter(isControllable);

  let out = [];
  if (allowAll) {
    out = controllable.map((t) => tabPublic(t, true));
    // Keep auth set in sync so a brief allowAll flip still has coverage.
    const set = new Set(controllable.map((t) => t.id));
    await saveAuthSet(set);
  } else {
    const set = await loadAuthSet();
    // If the allow-list is empty, auto-upgrade to allow-all (first-run / broken state).
    if (set.size === 0 && controllable.length > 0) {
      await setAllowAll(true);
      out = controllable.map((t) => tabPublic(t, true));
      await saveAuthSet(new Set(controllable.map((t) => t.id)));
    } else {
      for (const chromeId of set) {
        try {
          const tab = await chrome.tabs.get(chromeId);
          if (!isControllable(tab)) {
            set.delete(chromeId);
            continue;
          }
          out.push(tabPublic(tab, true));
        } catch {
          set.delete(chromeId);
        }
      }
      await saveAuthSet(set);
    }
  }

  await persistMaps();
  await writeDebug({
    event: "listAuthorizedTabs",
    allowAll: await getAllowAll(),
    open: all.length,
    controllable: controllable.length,
    returned: out.length,
    urls: out.slice(0, 20).map((t) => t.url || t.title || t.id),
  });
  return out;
}

export async function listAllTabsForPopup() {
  await hydrateMaps();
  const allowAll = await getAllowAll();
  const set = await loadAuthSet();
  const tabs = await queryAllTabs();
  const out = tabs
    .filter((t) => isControllable(t))
    .map((t) => {
      const vid = ensureVirtualId(t.id);
      const url = t.url || t.pendingUrl || "";
      return {
        chromeTabId: t.id,
        id: vid,
        title: t.title || "",
        url,
        domain: domainOf(url),
        authorized: allowAll || set.has(t.id),
        active: !!t.active,
        allowAll,
      };
    });
  await persistMaps();
  await writeDebug({
    event: "listAllTabsForPopup",
    allowAll,
    open: tabs.length,
    listed: out.length,
  });
  return out;
}

export async function requireAuthorizedVirtual(virtualId) {
  await hydrateMaps();
  let chromeId = resolveChromeTabId(virtualId);
  if (chromeId == null) {
    // Rebuild maps from live tabs
    const listed = await listAuthorizedTabs();
    const hit = listed.find((t) => t.id === virtualId);
    if (hit) {
      chromeId = resolveChromeTabId(virtualId);
    } else if (await getAllowAll()) {
      const all = await queryAllTabs();
      for (const t of all) {
        if (!isControllable(t)) continue;
        ensureVirtualId(t.id);
      }
      await persistMaps();
      chromeId = resolveChromeTabId(virtualId);
    }
  }
  if (chromeId == null) {
    const err = new Error(
      `Tab not found (${virtualId}). Call browser_tabs again for a fresh list.`
    );
    err.code = "TAB_NOT_FOUND";
    throw err;
  }
  if (!(await isAuthorized(chromeId))) {
    const err = new Error("Tab is not authorized — enable Control all tabs in the extension");
    err.code = "TAB_NOT_AUTHORIZED";
    throw err;
  }
  return chromeId;
}

/** Called on SW install/startup — ensure allow-all default is written and seeded. */
export async function bootstrapAllowAll() {
  try {
    const data = await chrome.storage.local.get(MODE_KEY);
    if (!(MODE_KEY in data) || data[MODE_KEY] === undefined || data[MODE_KEY] === null) {
      await setAllowAll(true);
    } else if (data[MODE_KEY] === true || data[MODE_KEY] === 1 || data[MODE_KEY] === "true") {
      await seedAuthSetFromOpenTabs();
    }
  } catch (e) {
    console.warn("[Tomo] bootstrapAllowAll", e);
  }
}

// Clean up maps when tabs close
chrome.tabs.onRemoved.addListener((tabId) => {
  const vid = chromeToVirtual.get(tabId);
  if (vid) {
    chromeToVirtual.delete(tabId);
    virtualToChrome.delete(vid);
    persistMaps();
  }
  loadAuthSet().then((set) => {
    if (set.has(tabId)) {
      set.delete(tabId);
      saveAuthSet(set);
    }
  });
});

// New tabs while allow-all is on → auto-include
chrome.tabs.onCreated.addListener((tab) => {
  getAllowAll().then(async (on) => {
    if (!on || tab?.id == null) return;
    const set = await loadAuthSet();
    set.add(tab.id);
    await saveAuthSet(set);
    ensureVirtualId(tab.id);
  });
});

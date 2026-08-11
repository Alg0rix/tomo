/** Per-tab element ref maps bound to snapshot versions. */

/** @typedef {{ ref: string, backendNodeId?: number, role?: string, name?: string, snapshotVersion: number }} ElementRef */

/** tabId → { version, refs: Map<string, ElementRef> } */
const byTab = new Map();

export function beginSnapshot(tabId) {
  const prev = byTab.get(tabId);
  const version = (prev?.version || 0) + 1;
  const entry = { version, refs: new Map() };
  byTab.set(tabId, entry);
  return version;
}

export function putRef(tabId, ref, data) {
  const entry = byTab.get(tabId);
  if (!entry) return;
  entry.refs.set(ref, { ...data, ref, snapshotVersion: entry.version });
}

export function getRef(tabId, ref, expectedVersion) {
  const entry = byTab.get(tabId);
  if (!entry) {
    const err = new Error("No snapshot for tab — call browser_snapshot first");
    err.code = "STALE_ELEMENT";
    err.recoverable = true;
    err.suggested_action = "browser_snapshot";
    throw err;
  }
  if (expectedVersion != null && Number(expectedVersion) !== entry.version) {
    const err = new Error("Element reference is no longer valid (stale snapshot).");
    err.code = "STALE_ELEMENT";
    err.recoverable = true;
    err.suggested_action = "browser_snapshot";
    throw err;
  }
  const hit = entry.refs.get(ref);
  if (!hit) {
    const err = new Error(`Element ref not found: ${ref}`);
    err.code = "ELEMENT_NOT_FOUND";
    err.recoverable = true;
    err.suggested_action = "browser_snapshot";
    throw err;
  }
  return hit;
}

export function currentVersion(tabId) {
  return byTab.get(tabId)?.version || 0;
}

export function clearTab(tabId) {
  byTab.delete(tabId);
}

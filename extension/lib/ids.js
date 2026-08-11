/** Virtual id helpers (never expose Chrome tabIds to Tomo). */

export function newId(prefix = "id") {
  const hex = crypto.randomUUID
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
    : `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
  return `${prefix}_${hex}`;
}

export function domainOf(url) {
  try {
    return new URL(url).hostname || "";
  } catch {
    return "";
  }
}

export const BLOCKED_PREFIXES = [
  "chrome://",
  "chrome-extension://",
  "devtools://",
  "edge://",
  "about:",
  "file://",
];

export function isBlockedUrl(url) {
  const u = String(url || "").toLowerCase();
  return BLOCKED_PREFIXES.some((p) => u.startsWith(p));
}

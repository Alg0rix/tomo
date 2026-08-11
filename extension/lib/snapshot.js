/** Accessibility-oriented snapshot for agents. */

import * as cdp from "./debugger.js";
import * as refs from "./refs.js";

const INTERACTIVE_ROLES = new Set([
  "button",
  "link",
  "textbox",
  "searchbox",
  "checkbox",
  "radio",
  "combobox",
  "listbox",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "option",
  "switch",
  "tab",
  "slider",
  "spinbutton",
  "treeitem",
]);

function roleOf(node) {
  return String(node.role?.value || node.role || "").toLowerCase();
}

function nameOf(node) {
  return String(node.name?.value || node.name || "").trim();
}

function walk(node, out, counter, depth = 0) {
  if (!node || depth > 40) return;
  const role = roleOf(node);
  const name = nameOf(node);
  const interesting =
    INTERACTIVE_ROLES.has(role) ||
    role === "heading" ||
    (role === "statictext" && name.length > 0 && name.length < 200);

  if (interesting && (name || INTERACTIVE_ROLES.has(role))) {
    counter.n += 1;
    const ref = INTERACTIVE_ROLES.has(role) ? `e${counter.n}` : `t${counter.n}`;
    out.push({
      ref,
      role: role || "unknown",
      name,
      backendNodeId: node.backendDOMNodeId || node.backendNodeId,
      interactive: INTERACTIVE_ROLES.has(role),
    });
  }
  const kids = node.childIds
    ? null
    : node.children || node.nodes || [];
  if (Array.isArray(node.children)) {
    for (const c of node.children) walk(c, out, counter, depth + 1);
  } else if (Array.isArray(kids) && kids.length && typeof kids[0] === "object") {
    for (const c of kids) walk(c, out, counter, depth + 1);
  }
}

function formatSnapshot(title, url, version, items) {
  const interactive = items.filter((i) => i.interactive);
  const content = items.filter((i) => !i.interactive);
  const lines = [
    "PAGE",
    `title: ${title || ""}`,
    `url: ${url || ""}`,
    `snapshot_version: ${version}`,
    "",
    "INTERACTIVE",
    "",
  ];
  for (const it of interactive.slice(0, 120)) {
    const label = it.name ? ` "${it.name.replace(/"/g, '\\"')}"` : "";
    lines.push(`[${it.ref}] ${it.role}${label}`);
  }
  if (!interactive.length) lines.push("(none detected)");
  lines.push("", "CONTENT", "");
  for (const it of content.slice(0, 40)) {
    const label = it.name ? ` "${it.name.replace(/"/g, '\\"')}"` : "";
    lines.push(`[${it.ref}] ${it.role}${label}`);
  }
  return lines.join("\n");
}

export async function captureSnapshot(tabId) {
  await cdp.attach(tabId);
  const tab = await chrome.tabs.get(tabId);
  const version = refs.beginSnapshot(tabId);

  let items = [];
  try {
    // Prefer full AX tree.
    const ax = await cdp.send(tabId, "Accessibility.getFullAXTree");
    const nodes = ax?.nodes || [];
    // Build tree from flat list with parentId / childIds when present.
    if (nodes.length && nodes[0].nodeId != null) {
      const byId = new Map(nodes.map((n) => [n.nodeId, n]));
      for (const n of nodes) {
        n.children = (n.childIds || [])
          .map((id) => byId.get(id))
          .filter(Boolean);
      }
      const roots = nodes.filter((n) => !n.parentId);
      const counter = { n: 0 };
      for (const r of roots.length ? roots : nodes.slice(0, 1)) {
        walk(r, items, counter);
      }
    } else {
      const counter = { n: 0 };
      for (const n of nodes) walk(n, items, counter);
    }
  } catch {
    // Fallback: query common interactive selectors via Runtime.
    items = await fallbackDomSnapshot(tabId);
  }

  for (const it of items) {
    if (it.interactive || it.backendNodeId) {
      refs.putRef(tabId, it.ref, {
        backendNodeId: it.backendNodeId,
        role: it.role,
        name: it.name,
      });
    }
  }

  const text = formatSnapshot(tab.title, tab.url, version, items);
  return {
    success: true,
    tab: {
      id: null, // filled by caller with virtual id
      title: tab.title || "",
      url: tab.url || "",
    },
    snapshot_version: version,
    snapshot: text,
  };
}

async function fallbackDomSnapshot(tabId) {
  const expr = `(() => {
    const sel = 'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],[contenteditable="true"]';
    const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 80);
    return nodes.map((el, i) => {
      const role = el.getAttribute('role') || el.tagName.toLowerCase();
      const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || '').trim().slice(0, 120);
      return { index: i, role, name, tag: el.tagName.toLowerCase() };
    });
  })()`;
  const res = await cdp.send(tabId, "Runtime.evaluate", {
    expression: expr,
    returnByValue: true,
  });
  const list = res?.result?.value || [];
  const items = [];
  for (let i = 0; i < list.length; i++) {
    const it = list[i];
    const ref = `e${i + 1}`;
    items.push({
      ref,
      role: String(it.role || "button").toLowerCase(),
      name: String(it.name || ""),
      interactive: true,
      backendNodeId: undefined,
      _index: it.index,
    });
  }
  // Store index-based resolution in ref map via synthetic backendNodeId = -index-1
  for (const it of items) {
    refs.putRef(tabId, it.ref, {
      backendNodeId: -(it._index + 1),
      role: it.role,
      name: it.name,
    });
  }
  return items;
}

export async function extractText(tabId, ref, snapshotVersion) {
  await cdp.attach(tabId);
  if (ref) {
    const r = refs.getRef(tabId, ref, snapshotVersion);
    if (r.backendNodeId && r.backendNodeId > 0) {
      const { object } = await cdp.send(tabId, "DOM.resolveNode", {
        backendNodeId: r.backendNodeId,
      });
      const res = await cdp.send(tabId, "Runtime.callFunctionOn", {
        objectId: object.objectId,
        functionDeclaration: "function() { return (this.innerText || this.textContent || '').slice(0, 20000); }",
        returnByValue: true,
      });
      return String(res?.result?.value || "");
    }
  }
  const res = await cdp.send(tabId, "Runtime.evaluate", {
    expression:
      "(() => { const m = document.querySelector('main,article,[role=main]'); return ((m||document.body).innerText||'').slice(0, 50000); })()",
    returnByValue: true,
  });
  return String(res?.result?.value || "");
}

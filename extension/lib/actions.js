/** High-level browser actions over CDP. */

import * as cdp from "./debugger.js";
import * as refs from "./refs.js";
import { isBlockedUrl } from "./ids.js";

async function resolveObjectId(tabId, ref, snapshotVersion) {
  const r = refs.getRef(tabId, ref, snapshotVersion);
  if (r.backendNodeId != null && r.backendNodeId > 0) {
    const { object } = await cdp.send(tabId, "DOM.resolveNode", {
      backendNodeId: r.backendNodeId,
    });
    return { objectId: object.objectId, meta: r };
  }
  if (r.backendNodeId != null && r.backendNodeId < 0) {
    // Fallback index from DOM snapshot.
    const index = -r.backendNodeId - 1;
    const res = await cdp.send(tabId, "Runtime.evaluate", {
      expression: `(() => {
        const sel = 'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],[contenteditable="true"]';
        const nodes = Array.from(document.querySelectorAll(sel));
        return nodes[${index}] || null;
      })()`,
      returnByValue: false,
    });
    if (!res?.result?.objectId) {
      const err = new Error("Element not interactable");
      err.code = "ELEMENT_NOT_INTERACTABLE";
      throw err;
    }
    return { objectId: res.result.objectId, meta: r };
  }
  const err = new Error("Element not interactable");
  err.code = "ELEMENT_NOT_INTERACTABLE";
  throw err;
}

async function centerOf(tabId, objectId) {
  const box = await cdp.send(tabId, "Runtime.callFunctionOn", {
    objectId,
    functionDeclaration: `function() {
      this.scrollIntoView({ block: 'center', inline: 'center' });
      const r = this.getBoundingClientRect();
      return { x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height };
    }`,
    returnByValue: true,
  });
  return box?.result?.value || { x: 0, y: 0 };
}

export async function click(tabId, ref, snapshotVersion) {
  await cdp.attach(tabId);
  const { objectId, meta } = await resolveObjectId(tabId, ref, snapshotVersion);
  // Sensitive label heuristic (design §23)
  if (meta?.name && /\b(delete|purchase|pay|transfer|submit|checkout)\b/i.test(meta.name)) {
    return {
      success: false,
      error: {
        code: "CONFIRMATION_REQUIRED",
        message: `Click on sensitive control requires confirmation: ${meta.name}`,
        recoverable: true,
        action: { type: "browser.click", target: meta.name },
      },
    };
  }
  const pt = await centerOf(tabId, objectId);
  await cdp.send(tabId, "Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: pt.x,
    y: pt.y,
    button: "left",
    clickCount: 1,
  });
  await cdp.send(tabId, "Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: pt.x,
    y: pt.y,
    button: "left",
    clickCount: 1,
  });
  await sleep(300);
  return pageResult(tabId, true);
}

export async function typeText(tabId, ref, text, submit, snapshotVersion) {
  await cdp.attach(tabId);
  const { objectId } = await resolveObjectId(tabId, ref, snapshotVersion);
  await cdp.send(tabId, "Runtime.callFunctionOn", {
    objectId,
    functionDeclaration: `function() {
      this.focus();
      if (this.select) try { this.select(); } catch(e) {}
    }`,
  });
  // Clear existing when input/textarea
  await cdp.send(tabId, "Runtime.callFunctionOn", {
    objectId,
    functionDeclaration: `function() {
      if ('value' in this) { this.value = ''; this.dispatchEvent(new Event('input', { bubbles: true })); }
    }`,
  });
  await cdp.send(tabId, "Input.insertText", { text: String(text ?? "") });
  if (submit) {
    await cdp.send(tabId, "Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    await cdp.send(tabId, "Input.dispatchKeyEvent", {
      type: "keyUp",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
    });
    await sleep(400);
  }
  return pageResult(tabId, true);
}

export async function press(tabId, key, ref, snapshotVersion) {
  await cdp.attach(tabId);
  if (ref) {
    const { objectId } = await resolveObjectId(tabId, ref, snapshotVersion);
    await cdp.send(tabId, "Runtime.callFunctionOn", {
      objectId,
      functionDeclaration: "function(){ this.focus(); }",
    });
  }
  const k = String(key || "");
  await cdp.send(tabId, "Input.dispatchKeyEvent", {
    type: "keyDown",
    key: k,
    code: k.length === 1 ? `Key${k.toUpperCase()}` : k,
  });
  await cdp.send(tabId, "Input.dispatchKeyEvent", {
    type: "keyUp",
    key: k,
    code: k.length === 1 ? `Key${k.toUpperCase()}` : k,
  });
  return pageResult(tabId, true);
}

export async function selectOption(tabId, ref, value, snapshotVersion) {
  await cdp.attach(tabId);
  const { objectId } = await resolveObjectId(tabId, ref, snapshotVersion);
  const res = await cdp.send(tabId, "Runtime.callFunctionOn", {
    objectId,
    functionDeclaration: `function(val) {
      if (this.tagName !== 'SELECT') return false;
      const opts = Array.from(this.options);
      let hit = opts.find(o => o.value === val || o.text === val);
      if (!hit) hit = opts.find(o => (o.text||'').includes(val));
      if (!hit) return false;
      this.value = hit.value;
      this.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }`,
    arguments: [{ value: String(value ?? "") }],
    returnByValue: true,
  });
  if (!res?.result?.value) {
    return {
      success: false,
      error: {
        code: "ELEMENT_NOT_INTERACTABLE",
        message: "Could not select option",
        recoverable: true,
        suggested_action: "browser_snapshot",
      },
    };
  }
  return pageResult(tabId, true);
}

export async function scroll(tabId, direction, amount, ref, snapshotVersion) {
  await cdp.attach(tabId);
  if (ref) {
    const { objectId } = await resolveObjectId(tabId, ref, snapshotVersion);
    await cdp.send(tabId, "Runtime.callFunctionOn", {
      objectId,
      functionDeclaration: "function(){ this.scrollIntoView({ block: 'center' }); }",
    });
    return pageResult(tabId, true);
  }
  const d = String(direction || "down").toLowerCase();
  const n = Number(amount) || 600;
  let dx = 0;
  let dy = 0;
  if (d === "up") dy = -n;
  else if (d === "down") dy = n;
  else if (d === "left") dx = -n;
  else if (d === "right") dx = n;
  else dy = n;
  await cdp.send(tabId, "Runtime.evaluate", {
    expression: `window.scrollBy(${dx}, ${dy})`,
  });
  return pageResult(tabId, true);
}

export async function navigate(tabId, url) {
  if (isBlockedUrl(url)) {
    return {
      success: false,
      error: {
        code: "BLOCKED_ORIGIN",
        message: `Navigation to privileged URL is blocked: ${url}`,
        recoverable: false,
      },
    };
  }
  await cdp.attach(tabId);
  try {
    await cdp.send(tabId, "Page.navigate", { url: String(url) });
    await sleep(800);
    return pageResult(tabId, true);
  } catch (e) {
    return {
      success: false,
      error: {
        code: "NAVIGATION_FAILED",
        message: String(e && e.message ? e.message : e),
        recoverable: true,
      },
    };
  }
}

export async function historyGo(tabId, delta) {
  await cdp.attach(tabId);
  await cdp.send(tabId, "Runtime.evaluate", {
    expression: `history.go(${Number(delta) || 0})`,
  });
  await sleep(500);
  return pageResult(tabId, true);
}

export async function wait(tabId, ms) {
  const t = Math.min(Math.max(Number(ms) || 1000, 0), 10000);
  await sleep(t);
  return pageResult(tabId, false);
}

export async function screenshot(tabId) {
  await cdp.attach(tabId);
  const res = await cdp.send(tabId, "Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
  });
  const tab = await chrome.tabs.get(tabId);
  return {
    success: true,
    image_base64: res?.data || "",
    page: { title: tab.title || "", url: tab.url || "" },
  };
}

async function pageResult(tabId, changed) {
  const tab = await chrome.tabs.get(tabId);
  return {
    success: true,
    changed: !!changed,
    page: {
      title: tab.title || "",
      url: tab.url || "",
    },
  };
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

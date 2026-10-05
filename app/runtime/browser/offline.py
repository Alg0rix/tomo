"""Offline document rendering inside the caller's restricted container.

Runs the bundled CloakBrowser/Chromium binary through its real Python
``launch()`` workflow (not just the ``cloak-chromium`` CLI) as a non-root
user in the read-only chat container. The browser profile, user-data dir
and Playwright-owned caches live under the private writable ``/home/chat``
tmpfs (``$HOME``/``$XDG_CACHE_HOME``), never under the read-only image
paths — that is the compatibility contract this module pins:

* ``CLOAKBROWSER_CACHE_DIR``/``CLOAKBROWSER_AUTO_UPDATE=false`` come from the
  image (pre-downloaded binary, no network refresh);
* ``PLAYWRIGHT_BROWSERS_PATH`` is left unset so Playwright resolves under
  the user cache; the ephemeral ``launch()`` profile defaults under tmp;
* the staged script refuses ``http(s)``/``ws(s)`` targets: offline renders
  cannot reach the network even if one were present.

Deterministic: the same HTML bytes always render the same title/text, with
no internet. A ``data:`` URL carries the document so no server round-trip
is involved at all.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_HTML_BYTES = 512 * 1024
_RENDER_TIMEOUT = 60.0

# stdlib-only driver preamble is impossible here (Playwright is required),
# so the staged script imports the image-bundled cloakbrowser/playwright.
# Everything else — JSON framing, size bounds, scheme refusal — is explicit.
_SCRIPT = r"""
import base64, json, sys

def fail(msg):
    sys.stdout.write(json.dumps({"ok": False, "error": msg}))
    sys.exit(0)

try:
    payload = json.loads(sys.stdin.read() or "{}")
except Exception as exc:
    fail(f"invalid render request: {exc}")

html = payload.get("html") or ""
if not isinstance(html, str) or not html:
    fail("html must be a non-empty string")
raw = html.encode("utf-8", errors="replace")
if len(raw) > %d:
    fail("html too large")
target = (payload.get("target") or "").strip().lower()
if target.startswith(("http://", "https://", "ws://", "wss://")):
    fail("online targets are unavailable to offline rendering")

try:
    from cloakbrowser import launch
except Exception as exc:
    fail(f"bundled browser unavailable: {exc}")

data_url = "data:text/html;base64," + base64.b64encode(raw).decode("ascii")
browser = None
try:
    browser = launch(headless=True, args=[
        "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
        "--disable-extensions", "--mute-audio",
    ])
    page = browser.new_page()
    # No network is available (container network=none); subresources and
    # websockets simply fail to load. Only the staged document is read.
    page.goto(data_url, wait_until="domcontentloaded", timeout=30000)
    title = page.title()
    try:
        text = page.inner_text("body") or ""
    except Exception:
        text = ""
    sys.stdout.write(json.dumps({"ok": True, "title": title or "", "text": text[:20000]}))
except Exception as exc:
    fail(f"render failed: {type(exc).__name__}")
finally:
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
""" % (_MAX_HTML_BYTES,)


def render_offline_html(context: Any, html: str, *, timeout: float = _RENDER_TIMEOUT) -> dict[str, Any]:
    """Render *html* offline in the caller's container; return title/text.

    Raises :class:`AccessDenied` for bad input and
    :class:`AccessUnavailable` when the container/browser backend is down.
    Online targets are refused here (use the online path in
    :mod:`app.runtime.tools.browser`, which fetches first under egress).
    """
    from app.runtime.access import AccessDenied, AccessUnavailable
    from app.runtime.isolation import tool_dispatch

    if not isinstance(html, str) or not html.strip():
        raise AccessDenied("browser render needs a non-empty document")
    if len(html.encode("utf-8", errors="replace")) > _MAX_HTML_BYTES:
        raise AccessDenied("browser document too large")
    backend = getattr(tool_dispatch, "backend", None)
    if backend is None:
        raise AccessUnavailable("Container browser backend is unavailable")
    to = max(5.0, min(float(timeout or _RENDER_TIMEOUT), 120.0))
    request = json.dumps({"html": html, "target": "data:"})
    try:
        result = backend.execute(
            context,
            ["python", "-c", _SCRIPT],
            stdin=request,
            timeout=to,
        )
    except Exception as exc:
        raise AccessUnavailable(f"Container browser backend is unavailable: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise AccessUnavailable(
            f"Container browser backend failed: {(result.stderr or '').strip()[:200]}"
        )
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except Exception as exc:
        raise AccessUnavailable("Container browser returned an unreadable result") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        err = str((payload or {}).get("error") or "render failed")
        if "unavailable" in err.lower():
            raise AccessUnavailable(f"Container browser is unavailable: {err[:200]}")
        raise AccessDenied(err[:300])
    return {"title": str(payload.get("title") or ""), "text": str(payload.get("text") or "")}


__all__ = ["render_offline_html"]

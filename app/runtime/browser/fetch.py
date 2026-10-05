"""Scoped online-document retrieval for the browser tool.

Production SSRF policy is never relaxed here: every candidate URL — the
initial navigation target AND every redirect hop — passes the shared
:func:`app.runtime.tools.web_fetch._check_url` guard (scheme allowlist,
DNS resolution, private/loopback/link-local/reserved/multicast rejection).

Redirects are followed manually (``follow_redirects=False``) so each hop is
re-validated, exactly like ``web_fetch``. Subresources, XHR/fetch and
``ws(s)://`` sockets are never retrieved live by this module at all: the
caller renders the single fetched document offline in a ``network=none``
container, where they fail closed by construction.

Test-origin allowance: browser integration tests serve a loopback fixture
server. Production SSRF would (correctly) block it, so this module honors
``TOMO_BROWSER_TEST_ORIGIN`` — an exact ``host:port`` pair, loopback only —
when it is set. The variable is unset in production, where behavior is then
byte-identical to ``web_fetch``. There is no user-controlled proxy setting:
arbitrary proxy configuration is not accepted from tool arguments.
"""

from __future__ import annotations

import ipaddress
import os
import threading
from urllib.parse import urljoin, urlparse

import httpx2

_TIMEOUT = 15.0
_DNS_TIMEOUT = 5.0
_OVERALL_TIMEOUT = 20.0
_MAX_REDIRECTS = 5
_MAX_HTML_BYTES = 512 * 1024

_TEST_ORIGIN_ENV = "TOMO_BROWSER_TEST_ORIGIN"


def test_origin_allowlist() -> tuple[str, int] | None:
    """Parse ``TOMO_BROWSER_TEST_ORIGIN`` (``host:port``) or return None.

    Only loopback hosts are honored; anything else is ignored (fail closed).
    """
    raw = (os.environ.get(_TEST_ORIGIN_ENV) or "").strip()
    if not raw:
        return None
    host, sep, port = raw.rpartition(":")
    host = host.strip().lower().strip("[]")
    if not sep or not host or not port.strip().isdigit():
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host != "localhost":
            return None
        ip = ipaddress.ip_address("127.0.0.1")
    if not ip.is_loopback:
        return None
    return host, int(port.strip())


def _is_test_origin(url: str) -> bool:
    allowed = test_origin_allowlist()
    if allowed is None:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host != allowed[0] and not (allowed[0] == "127.0.0.1" and host == "localhost"):
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return port == allowed[1]


def check_browser_url(url: str) -> str | None:
    """Validate one URL for browser navigation/fetch. Error string or None.

    The trusted test origin (when configured) is allowed through; every
    other URL faces the production SSRF guard unchanged. ``ws(s)`` schemes
    are rejected: live sockets are never opened by the browser tool.
    """
    from app.runtime.tools.web_fetch import _check_url

    text = (url or "").strip()
    if not text:
        return "Error: URL must be a non-empty string"
    scheme = urlparse(text).scheme.lower()
    if scheme in {"ws", "wss"}:
        return "Error: live websocket connections are unavailable to the browser tool"
    if scheme not in {"http", "https"}:
        return "Error: only http and https URLs are allowed"
    if _is_test_origin(text):
        return None
    return _check_url(text)


def fetch_document(url: str, *, timeout: float = _TIMEOUT) -> tuple[bytes, str] | str:
    """GET *url* with per-hop validation; ``(body, final_url)`` or error.

    Every redirect ``Location`` is re-validated with :func:`check_browser_url`
    before following, so a public initial URL cannot bounce into an internal
    endpoint. Bodies are capped; only HTML-ish/text content is returned.
    """
    err = check_browser_url(url)
    if err:
        return err
    box: list[tuple[bytes, str] | str] = []

    def work() -> None:
        try:
            box.append(_fetch(url, timeout))
        except Exception as exc:  # noqa: BLE001 — surfaced as an error string
            box.append(f"Error: could not fetch URL: {exc}")

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(_OVERALL_TIMEOUT)
    if box:
        return box[0]
    return f"Error: request timed out after {_OVERALL_TIMEOUT:g}s"


def _fetch(url: str, timeout: float) -> tuple[bytes, str] | str:
    to = max(1.0, min(float(timeout or _TIMEOUT), _TIMEOUT))
    try:
        with httpx2.Client(timeout=to, follow_redirects=False) as client:
            current = url
            resp = None
            for _ in range(_MAX_REDIRECTS + 1):
                hop_err = check_browser_url(current)
                if hop_err:
                    return hop_err
                resp = client.get(current)
                status = int(getattr(resp, "status_code", 0) or 0)
                if 300 <= status < 400:
                    loc = (resp.headers.get("location") or "").strip()
                    if not loc:
                        return "Error: redirect with empty Location"
                    current = urljoin(str(resp.url), loc)
                    continue
                break
            else:
                return "Error: too many redirects"
            assert resp is not None
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if content_type and not any(
                marker in content_type
                for marker in ("text/html", "text/plain", "application/xhtml")
            ):
                return f"Error: unsupported content type: {content_type.split(';')[0].strip() or 'unknown'}"
            body = resp.content or b""
            if len(body) > _MAX_HTML_BYTES:
                body = body[:_MAX_HTML_BYTES]
            return body, str(resp.url)
    except httpx2.TimeoutException:
        return f"Error: request timed out after {to:g}s"
    except httpx2.HTTPStatusError as exc:
        return f"Error: HTTP {exc.response.status_code} fetching {url}"
    except httpx2.HTTPError as exc:
        return f"Error: could not fetch URL: {exc}"
    except OSError as exc:
        return f"Error: could not fetch URL: {exc}"


__all__ = ["check_browser_url", "fetch_document", "test_origin_allowlist"]

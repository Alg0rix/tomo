"""web_fetch tool — HTTP GET a URL with SSRF guards and size limits."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_TIMEOUT = 15.0
_MAX_CHARS = 100_000
_MAX_REDIRECTS = 5


def _is_blocked_host(hostname: str) -> str | None:
    """Return an error string if ``hostname`` resolves to a private/loopback IP."""
    host = (hostname or "").strip().lower()
    if not host:
        return "Error: URL host is empty"
    if host in {"localhost", "metadata.google.internal"}:
        return "Error: private/loopback hosts are blocked"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"Error: could not resolve host: {exc}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"Error: blocked address {ip_str} (private/loopback)"
    return None


def _check_url(url: str) -> str | None:
    """Validate scheme/host and SSRF blocklist. Return error string or None."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "Error: only http and https URLs are allowed"
    if not parsed.hostname:
        return "Error: URL host is empty"
    return _is_blocked_host(parsed.hostname)


def run(arguments: dict[str, Any]) -> str:
    """Fetch ``url`` and return truncated response text; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: web_fetch expects a dict of arguments"
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        return "Error: 'url' argument must be a non-empty string"
    url = url.strip()

    blocked = _check_url(url)
    if blocked:
        return blocked

    try:
        # Manual redirects so each hop is SSRF-checked (httpx follow_redirects
        # would skip re-validation of Location targets).
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
            current = url
            resp: httpx.Response | None = None
            for _ in range(_MAX_REDIRECTS + 1):
                hop_err = _check_url(current)
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
            text = resp.text
    except httpx.TimeoutException:
        return f"Error: request timed out after {_TIMEOUT:g}s"
    except httpx.HTTPStatusError as exc:
        return f"Error: HTTP {exc.response.status_code} fetching {url}"
    except httpx.HTTPError as exc:
        return f"Error: could not fetch URL: {exc}"
    except OSError as exc:
        return f"Error: could not fetch URL: {exc}"

    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + f"\n...[truncated, {len(text)} chars total]"
    return text if text else "(empty response)"


__all__ = ["run"]

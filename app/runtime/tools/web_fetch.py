"""web_fetch tool — HTTP GET a URL with SSRF guards and size limits."""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

_TIMEOUT = 15.0
_MAX_CHARS = 100_000
_MAX_REDIRECTS = 5

_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
_NOISE_TAGS = frozenset({"nav", "footer", "aside"})
_BREAK_TAGS = frozenset(
    {"address", "article", "blockquote", "br", "div", "h1", "h2", "h3",
     "h4", "h5", "h6", "hr", "li", "main", "p", "pre", "section", "tr"}
)


class _ReadableHTMLParser(HTMLParser):
    """Collect readable body/main text without page chrome and executable code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body: list[str] = []
        self.main: list[str] = []
        self.title: list[str] = []
        self._body_depth = 0
        self._main_depth = 0
        self._hidden_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "body":
            self._body_depth += 1
        if tag == "main":
            self._main_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag in _HIDDEN_TAGS or tag in _NOISE_TAGS:
            self._hidden_depth += 1
        if tag in _BREAK_TAGS:
            self._append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() in _BREAK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _BREAK_TAGS:
            self._append("\n")
        if tag in _HIDDEN_TAGS or tag in _NOISE_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
        if tag == "main":
            self._main_depth = max(0, self._main_depth - 1)
        if tag == "body":
            self._body_depth = max(0, self._body_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._title_depth and not self._hidden_depth:
            self.title.append(data)
        self._append(data)

    def _append(self, text: str) -> None:
        if self._hidden_depth:
            return
        if self._body_depth:
            self.body.append(text)
        if self._main_depth:
            self.main.append(text)


def _readable_html(text: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(text)
    parser.close()
    chunks = parser.main or parser.body
    content = "".join(chunks)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in content.splitlines()]
    readable = "\n".join(line for line in lines if line)
    title = re.sub(r"\s+", " ", "".join(parser.title)).strip()
    if title and title not in readable[: max(200, len(title))]:
        readable = f"{title}\n\n{readable}" if readable else title
    return readable


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
            content_type = (resp.headers.get("content-type") or "").lower()
    except httpx.TimeoutException:
        return f"Error: request timed out after {_TIMEOUT:g}s"
    except httpx.HTTPStatusError as exc:
        return f"Error: HTTP {exc.response.status_code} fetching {url}"
    except httpx.HTTPError as exc:
        return f"Error: could not fetch URL: {exc}"
    except OSError as exc:
        return f"Error: could not fetch URL: {exc}"

    if "html" in content_type or re.match(r"\s*(?:<!doctype\s+html|<html\b)", text, re.I):
        text = _readable_html(text)

    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + f"\n...[truncated, {len(text)} chars total]"
    return text if text else "(empty response)"


__all__ = ["run"]

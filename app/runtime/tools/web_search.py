"""web_search tool — DuckDuckGo HTML results (no API key).

Falls back to the Instant Answer API when HTML parsing yields nothing.
Handles empty / non-JSON Instant Answer bodies without crashing.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx

_TIMEOUT = 15.0
_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_IA_ENDPOINT = "https://api.duckduckgo.com/"
_MAX_RESULTS = 5
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Tomo/1.0"
)

_RESULT_A_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div|span)',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub("", text or "")).strip()


def _unwrap_ddg_url(href: str) -> str:
    """Resolve DDG redirect links (//duckduckgo.com/l/?uddg=…) to the target URL."""
    raw = unescape((href or "").strip())
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    return raw


def _format_blocks(blocks: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for idx, (title, extra) in enumerate(blocks, start=1):
        lines.append(f"{idx}. {title}")
        for part in str(extra).splitlines():
            if part.strip():
                lines.append(f"   {part.strip()}")
    return "\n".join(lines)


def _search_html(client: httpx.Client, query: str) -> list[tuple[str, str]]:
    """Parse top results from DuckDuckGo HTML search."""
    resp = client.get(
        _HTML_ENDPOINT,
        params={"q": query},
        headers={"User-Agent": _UA, "Accept": "text/html"},
    )
    resp.raise_for_status()
    text = resp.text or ""
    titles = _RESULT_A_RE.findall(text)
    snippets = [_strip_html(s) for s in _SNIPPET_RE.findall(text)]

    results: list[tuple[str, str]] = []
    for i, (href, title_html) in enumerate(titles):
        if len(results) >= _MAX_RESULTS:
            break
        title = _strip_html(title_html)
        if not title:
            continue
        url = _unwrap_ddg_url(href)
        snippet = snippets[i] if i < len(snippets) else ""
        body_parts = [p for p in (snippet, url) if p]
        results.append((title, "\n".join(body_parts)))
    return results


def _collect_ia_results(data: dict[str, Any], query: str) -> list[tuple[str, str]]:
    """Return (title_or_text, body) pairs from a DDG Instant Answer payload."""
    results: list[tuple[str, str]] = []
    abstract = (data.get("AbstractText") or "").strip()
    if abstract:
        title = (data.get("Heading") or query).strip() or query
        url = (data.get("AbstractURL") or "").strip()
        body = abstract if not url else f"{abstract}\n{url}"
        results.append((title, body))

    related = data.get("RelatedTopics") or []
    if not isinstance(related, list):
        return results[:_MAX_RESULTS]

    def _add(item: dict[str, Any]) -> None:
        if len(results) >= _MAX_RESULTS:
            return
        text = (item.get("Text") or "").strip()
        href = (item.get("FirstURL") or "").strip()
        if text:
            results.append((text, href))

    for item in related:
        if len(results) >= _MAX_RESULTS:
            break
        if not isinstance(item, dict):
            continue
        topics = item.get("Topics")
        if isinstance(topics, list):
            for sub in topics:
                if isinstance(sub, dict):
                    _add(sub)
                if len(results) >= _MAX_RESULTS:
                    break
            continue
        _add(item)
    return results[:_MAX_RESULTS]


def _search_instant_answer(
    client: httpx.Client, query: str
) -> list[tuple[str, str]]:
    """Best-effort Instant Answer; empty/non-JSON bodies return []."""
    params = urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
    )
    url = f"{_IA_ENDPOINT}?{params}"
    resp = client.get(url, headers={"User-Agent": _UA})
    resp.raise_for_status()
    raw = (resp.text or "").strip()
    if not raw:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return _collect_ia_results(data, query)


def run(arguments: dict[str, Any]) -> str:
    """Search the web for ``query``; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: web_search expects a dict of arguments"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    query = query.strip()

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            blocks = _search_html(client, query)
            if not blocks:
                blocks = _search_instant_answer(client, query)
    except httpx.TimeoutException:
        return f"Error: search timed out after {_TIMEOUT:g}s"
    except httpx.HTTPError as exc:
        return f"Error: search request failed: {exc}"
    except OSError as exc:
        return f"Error: search request failed: {exc}"

    if not blocks:
        return f"No results for {query!r}"
    return _format_blocks(blocks)


__all__ = ["run"]

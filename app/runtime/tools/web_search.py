"""web_search tool — DuckDuckGo Instant Answer API (no API key)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

_TIMEOUT = 15.0
_ENDPOINT = "https://api.duckduckgo.com/"
_MAX_RESULTS = 5


def _collect_results(data: dict[str, Any], query: str) -> list[tuple[str, str]]:
    """Return (title_or_text, url) pairs from a DDG Instant Answer payload."""
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


def run(arguments: dict[str, Any]) -> str:
    """Search the web for ``query``; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: web_search expects a dict of arguments"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    query = query.strip()

    params = urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
    )
    url = f"{_ENDPOINT}?{params}"

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Tomo/1.0"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return f"Error: search timed out after {_TIMEOUT:g}s"
    except httpx.HTTPError as exc:
        return f"Error: search request failed: {exc}"
    except (OSError, ValueError) as exc:
        return f"Error: search request failed: {exc}"

    if not isinstance(data, dict):
        return "Error: unexpected search response"

    blocks = _collect_results(data, query)
    if not blocks:
        return f"No results for {query!r}"

    lines: list[str] = []
    for idx, (title, extra) in enumerate(blocks, start=1):
        lines.append(f"{idx}. {title}")
        for part in str(extra).splitlines():
            if part.strip():
                lines.append(f"   {part.strip()}")
    return "\n".join(lines)


__all__ = ["run"]

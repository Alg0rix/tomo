"""``recall`` tool — keyword search over SQLite knowledge entries.

Returns a formatted string of top-k snippets for the agent loop. Empty or
non-matching queries yield a clear message (not an exception).
"""

from __future__ import annotations

from typing import Any


def _format_hits(hits: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, hit in enumerate(hits, start=1):
        tags = hit.get("tags") or []
        tag_s = f" [{', '.join(tags)}]" if tags else ""
        parts.append(
            f"{i}. {hit.get('title', '')}{tag_s}\n{hit.get('body', '')}".strip()
        )
    return "\n\n".join(parts)


def run(arguments: dict[str, Any]) -> str:
    """Tool backend entry point used by the registry."""
    if not isinstance(arguments, dict):
        return "Error: 'query' argument must be a string"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    limit = arguments.get("limit", 5)
    try:
        limit_i = int(limit) if limit is not None else 5
    except (TypeError, ValueError):
        return "Error: 'limit' must be an integer"

    # Lazy import avoids circular import with store ↔ registry at module load.
    from app.services import store

    hits = store.search_knowledge(query.strip(), limit=limit_i)
    if not hits:
        return f"No knowledge entries matched query: {query.strip()!r}"
    return _format_hits(hits)


__all__ = ["run"]

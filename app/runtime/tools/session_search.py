"""session_search tool — keyword search over persisted session messages."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Search session message content; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: session_search expects a dict of arguments"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    query = query.strip()
    limit = arguments.get("limit", 10)
    try:
        limit_i = int(limit) if limit is not None else 10
    except (TypeError, ValueError):
        return "Error: 'limit' must be an integer"

    from app.services import store
    from app.runtime.tools.user_ctx import current_user_id

    hits = store.search_messages(query, limit=limit_i, user_id=current_user_id())
    if not hits:
        return f"No messages matched query: {query!r}"
    lines: list[str] = []
    for i, hit in enumerate(hits, start=1):
        sid = hit.get("session_id", "")
        mtype = hit.get("type", "")
        content = (hit.get("content") or "").strip().replace("\n", " ")
        if len(content) > 160:
            content = content[:160] + "…"
        lines.append(f"{i}. [{sid}/{mtype}] {content}")
    return "\n".join(lines)


__all__ = ["run"]

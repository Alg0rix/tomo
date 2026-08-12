"""``recall_episodes`` tool — search concrete past experiences for this user."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: recall_episodes expects a dict of arguments"
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    limit = arguments.get("limit", 5)
    try:
        limit_i = int(limit) if limit is not None else 5
    except (TypeError, ValueError):
        return "Error: 'limit' must be an integer"

    from app.runtime.tools.user_ctx import current_user_id
    from app.services import store

    hits = store.search_episodes(
        query.strip(), limit=limit_i, user_id=current_user_id()
    )
    if not hits:
        return f"No episodes matched query: {query.strip()!r}"
    lines: list[str] = []
    for i, ep in enumerate(hits, start=1):
        title = (ep.get("title") or "").strip() or "Episode"
        body = (ep.get("content") or "").strip().replace("\n", " ")
        if len(body) > 220:
            body = body[:217] + "…"
        lines.append(f"{i}. [{ep.get('id')}] {title}: {body}")
    return "\n".join(lines)


__all__ = ["run"]

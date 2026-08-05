"""Memory read/write adapters over SQLite knowledge entries (Slice E).

Thin helpers used by tools and tests. Persistence lives in
:mod:`app.models.mixins.knowledge_entries` via the store facade.
"""

from __future__ import annotations

from typing import Any


def search(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    from app.services import store

    return store.search_knowledge(query, limit=limit)


def remember(
    title: str,
    body: str,
    tags: list[str] | None = None,
    *,
    confidence: float | None = None,
) -> dict[str, Any]:
    from app.services import store

    data: dict[str, Any] = {"title": title, "body": body, "tags": tags or []}
    if confidence is not None:
        data["confidence"] = confidence
    return store.create_knowledge_entry(data)


__all__ = ["search", "remember"]

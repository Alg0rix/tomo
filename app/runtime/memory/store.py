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
    title: str, body: str, tags: list[str] | None = None
) -> dict[str, Any]:
    from app.services import store

    return store.create_knowledge_entry(
        {"title": title, "body": body, "tags": tags or []}
    )


__all__ = ["search", "remember"]

"""forget_memory tool — delete a knowledge entry by id or query."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Delete a knowledge entry; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: forget_memory expects a dict of arguments"

    entry_id = arguments.get("id")
    query = arguments.get("query")

    from app.services import store

    if isinstance(entry_id, str) and entry_id.strip():
        eid = entry_id.strip()
        existing = store.get_knowledge_entry(eid)
        if existing is None:
            return f"Error: unknown knowledge id {eid!r}"
        ok = store.delete_knowledge_entry(eid)
        if not ok:
            return f"Error: could not delete knowledge id {eid!r}"
        return f"Forgot knowledge entry {eid!r}: {existing.get('title', '')}"

    if isinstance(query, str) and query.strip():
        hits = store.search_knowledge(query.strip(), limit=1)
        if not hits:
            return f"Error: no knowledge entries matched query: {query.strip()!r}"
        hit = hits[0]
        eid = hit["id"]
        ok = store.delete_knowledge_entry(eid)
        if not ok:
            return f"Error: could not delete knowledge id {eid!r}"
        return f"Forgot knowledge entry {eid!r}: {hit.get('title', '')}"

    return "Error: provide 'id' or 'query'"


__all__ = ["run"]

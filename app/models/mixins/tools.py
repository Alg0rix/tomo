"""Per-agent tool enablement — SQLite ``agent_tools`` table (Alpha Slice C).

Choice (documented): persist enable/disable in SQLite table ``agent_tools``
rather than settings JSON. Rows are ``(agent_id, tool_id, enabled)``. When an
agent has **no** rows yet, every registry tool is treated as enabled (default
opt-in). Saving the Tools panel upserts one row per known tool id.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def list_for_agent(
    conn: sqlite3.Connection, agent_id: str, catalog: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge registry ``catalog`` with persisted enablement for ``agent_id``."""
    rows = conn.execute(
        "SELECT tool_id, enabled FROM agent_tools WHERE agent_id=?",
        (agent_id,),
    ).fetchall()
    if not rows:
        return [dict(t, enabled=True) for t in catalog]
    enabled_map = {r["tool_id"]: bool(r["enabled"]) for r in rows}
    return [dict(t, enabled=enabled_map.get(t["id"], True)) for t in catalog]


def enabled_ids(
    conn: sqlite3.Connection, agent_id: str, all_ids: list[str]
) -> set[str]:
    """Return the set of tool ids enabled for ``agent_id``."""
    rows = conn.execute(
        "SELECT tool_id, enabled FROM agent_tools WHERE agent_id=?",
        (agent_id,),
    ).fetchall()
    if not rows:
        return set(all_ids)
    enabled_map = {r["tool_id"]: bool(r["enabled"]) for r in rows}
    return {tid for tid in all_ids if enabled_map.get(tid, True)}


def set_for_agent(
    conn: sqlite3.Connection,
    agent_id: str,
    enabled: dict[str, bool],
    known_ids: list[str],
) -> list[str]:
    """Upsert enablement for ``agent_id``; return the enabled tool id list.

    Unknown tool ids in ``enabled`` are ignored. Tools in ``known_ids`` missing
    from ``enabled`` default to disabled when any explicit map is provided.
    Also refreshes ``agents.tool_count``.
    """
    known = set(known_ids)
    conn.execute("DELETE FROM agent_tools WHERE agent_id=?", (agent_id,))
    enabled_list: list[str] = []
    for tool_id in sorted(known):
        is_on = bool(enabled.get(tool_id, False))
        conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id, enabled) VALUES (?,?,?)",
            (agent_id, tool_id, 1 if is_on else 0),
        )
        if is_on:
            enabled_list.append(tool_id)
    conn.execute(
        "UPDATE agents SET tool_count=? WHERE id=?",
        (len(enabled_list), agent_id),
    )
    conn.commit()
    return enabled_list


__all__ = ["list_for_agent", "enabled_ids", "set_for_agent"]

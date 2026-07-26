"""Agent records — CRUD over the ``agents`` table.

Busy state is injected by the caller (the store facade's in-memory
``BusyState``); the ``agents`` table has no ``busy`` column.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any


def _now() -> float:
    return time.time()


def _row_to_agent(row: sqlite3.Row, busy_ids: set[str]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "model_id": row["model_id"],
        "role": row["role"],
        "enabled": bool(row["enabled"]),
        "is_super": bool(row["is_super"]),
        "tool_count": row["tool_count"],
        "channel_count": row["channel_count"],
        "skill_count": row["skill_count"],
        "busy": row["id"] in busy_ids,
        "created_at": row["created_at"],
    }


def list_agents(conn: sqlite3.Connection, busy_ids: set[str]) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM agents ORDER BY is_super DESC, created_at ASC").fetchall()
    return [_row_to_agent(r, busy_ids) for r in rows]


def get_coordinator(
    conn: sqlite3.Connection, busy_ids: set[str] | None = None
) -> dict[str, Any] | None:
    """Return the swarm coordinator agent.

    Prefers an enabled ``is_super`` agent; falls back to the first enabled
    agent. Returns ``None`` when no enabled agents exist.
    """
    busy = busy_ids if busy_ids is not None else set()
    row = conn.execute(
        "SELECT * FROM agents WHERE enabled=1 AND is_super=1 "
        "ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM agents WHERE enabled=1 ORDER BY is_super DESC, created_at ASC LIMIT 1"
        ).fetchone()
    return _row_to_agent(row, busy) if row else None


def get_agent(
    conn: sqlite3.Connection, agent_id: str, busy_ids: set[str]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return _row_to_agent(row, busy_ids) if row else None


def create_agent(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM agents WHERE id=?", (data["id"],)).fetchone():
        raise ValueError("Agent ID already exists")
    created_at = _now()
    conn.execute(
        "INSERT INTO agents (id, name, description, model_id, role, enabled, is_super, "
        "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["id"],
            data.get("name", data["id"]),
            data.get("description", ""),
            data.get("model_id") or "",
            data.get("role") or "",
            1, 0, 0, 0, 0, created_at,
        ),
    )
    conn.commit()
    return {
        "id": data["id"],
        "name": data.get("name", data["id"]),
        "description": data.get("description", ""),
        "model_id": data.get("model_id") or "",
        "role": data.get("role") or "",
        "enabled": True,
        "is_super": False,
        "tool_count": 0,
        "channel_count": 0,
        "skill_count": 0,
        "busy": False,
        "created_at": created_at,
    }


def update_agent(
    conn: sqlite3.Connection, agent_id: str, data: dict[str, Any], busy_ids: set[str]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        return None
    sets: list[str] = []
    params: list[Any] = []
    for key in ("name", "description", "model_id", "role"):
        if key in data and data[key] is not None:
            sets.append(f"{key}=?")
            params.append(data[key])
    if "enabled" in data and data["enabled"] is not None:
        sets.append("enabled=?")
        params.append(1 if data["enabled"] else 0)
    if sets:
        params.append(agent_id)
        conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    return _row_to_agent(row, busy_ids)


def delete_agent(conn: sqlite3.Connection, agent_id: str) -> bool:
    if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
        return False
    # Drop sessions where this agent is the only member.
    solo = conn.execute(
        "SELECT s.id FROM sessions s "
        "WHERE EXISTS (SELECT 1 FROM session_agents sa WHERE sa.session_id=s.id AND sa.agent_id=?) "
        "AND (SELECT COUNT(*) FROM session_agents sa WHERE sa.session_id=s.id)=1",
        (agent_id,),
    ).fetchall()
    for s in solo:
        conn.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
    conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    # Reassign coordinators left NULL by ON DELETE SET NULL.
    orphans = conn.execute(
        "SELECT s.id FROM sessions s WHERE s.coordinator_id IS NULL "
        "AND EXISTS (SELECT 1 FROM session_agents sa WHERE sa.session_id=s.id)",
    ).fetchall()
    for o in orphans:
        first = conn.execute(
            "SELECT agent_id FROM session_agents WHERE session_id=? ORDER BY position LIMIT 1",
            (o["id"],),
        ).fetchone()
        if first:
            conn.execute(
                "UPDATE sessions SET coordinator_id=? WHERE id=?",
                (first["agent_id"], o["id"]),
            )
    conn.commit()
    return True


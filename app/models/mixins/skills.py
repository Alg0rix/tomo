"""Skills catalog + per-agent links — SQLite (Alpha Slice G)."""

from __future__ import annotations

import sqlite3
from typing import Any


def _agent_count(conn: sqlite3.Connection, skill_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM agent_skills WHERE skill_id=?",
        (skill_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _row_to_skill(row: sqlite3.Row, agent_count: int) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "tool_count": int(row["tool_count"] or 0),
        "agent_count": agent_count,
        "created_at": row["created_at"],
        "path": row["path"] if "path" in keys else "",
        "source": row["source"] if "source" in keys else "",
    }


def list_skills(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM skills ORDER BY name ASC").fetchall()
    return [_row_to_skill(r, _agent_count(conn, r["id"])) for r in rows]


def get_skill(conn: sqlite3.Connection, skill_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
    if not row:
        return None
    return _row_to_skill(row, _agent_count(conn, skill_id))


def list_for_agent(conn: sqlite3.Connection, agent_id: str) -> list[dict[str, Any]]:
    """All skills with ``assigned`` flag for ``agent_id``."""
    assigned = {
        r["skill_id"]
        for r in conn.execute(
            "SELECT skill_id FROM agent_skills WHERE agent_id=?", (agent_id,)
        ).fetchall()
    }
    return [dict(s, assigned=s["id"] in assigned) for s in list_skills(conn)]


def set_for_agent(
    conn: sqlite3.Connection, agent_id: str, skill_ids: list[str]
) -> list[dict[str, Any]]:
    """Replace agent↔skill links; refresh ``agents.skill_count``."""
    known = {r["id"] for r in conn.execute("SELECT id FROM skills").fetchall()}
    conn.execute("DELETE FROM agent_skills WHERE agent_id=?", (agent_id,))
    kept: list[str] = []
    for sid in skill_ids:
        if sid not in known:
            continue
        conn.execute(
            "INSERT INTO agent_skills (agent_id, skill_id) VALUES (?,?)",
            (agent_id, sid),
        )
        kept.append(sid)
    conn.execute(
        "UPDATE agents SET skill_count=? WHERE id=?",
        (len(kept), agent_id),
    )
    conn.commit()
    return list_for_agent(conn, agent_id)


def update_skill(
    conn: sqlite3.Connection, skill_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
    if not row:
        return None
    enabled = row["enabled"]
    if "enabled" in data and data["enabled"] is not None:
        enabled = 1 if data["enabled"] else 0
    name = data.get("name", row["name"])
    description = data.get("description", row["description"])
    version = data.get("version", row["version"])
    conn.execute(
        "UPDATE skills SET name=?, description=?, version=?, enabled=? WHERE id=?",
        (name, description, version, enabled, skill_id),
    )
    conn.commit()
    return get_skill(conn, skill_id)


def delete_skill(conn: sqlite3.Connection, skill_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM skills WHERE id=?", (skill_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM agent_skills WHERE skill_id=?", (skill_id,))
    conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    conn.commit()
    return True


__all__ = [
    "list_skills",
    "get_skill",
    "list_for_agent",
    "set_for_agent",
    "update_skill",
    "delete_skill",
]

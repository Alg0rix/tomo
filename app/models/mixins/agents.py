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


def _parse_workplace_ids(raw: Any) -> list[str]:
    import json

    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _row_to_agent(row: sqlite3.Row, busy_ids: set[str]) -> dict[str, Any]:
    keys = set(row.keys())
    scope = (
        row["workplace_scope"]
        if "workplace_scope" in keys and row["workplace_scope"]
        else "single"
    )
    wids = _parse_workplace_ids(
        row["workplace_ids_json"] if "workplace_ids_json" in keys else "[]"
    )
    primary = row["workplace_id"] if "workplace_id" in keys else ""
    if primary and primary not in wids and scope == "single":
        wids = [primary] if primary else wids
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "model_id": row["model_id"],
        "role": row["role"],
        "workplace_id": primary or "",
        "workplace_scope": scope,
        "workplace_ids": wids,
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


def list_enabled_agent_ids(conn: sqlite3.Connection) -> list[str]:
    """Enabled agents: super/coordinator first, then by created_at."""
    rows = conn.execute(
        "SELECT id FROM agents WHERE enabled=1 "
        "ORDER BY is_super DESC, created_at ASC"
    ).fetchall()
    return [r["id"] for r in rows]


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
    from app.models.ids import unique_id

    name = (data.get("name") or "").strip() or "agent"
    aid = unique_id(
        conn,
        "agents",
        name=name,
        prefix="",
        explicit=(data.get("id") or None),
    )
    import json

    created_at = _now()
    workplace_id, scope, wids = _normalize_workplace_fields(data)
    conn.execute(
        "INSERT INTO agents (id, name, description, model_id, role, workplace_id, "
        "workplace_scope, workplace_ids_json, enabled, "
        "is_super, tool_count, channel_count, skill_count, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            aid,
            name,
            data.get("description", ""),
            data.get("model_id") or "",
            data.get("role") or "",
            workplace_id,
            scope,
            json.dumps(wids),
            1, 0, 0, 0, 0, created_at,
        ),
    )
    conn.commit()
    # Swarm default: new agents join every multi-agent session automatically.
    try:
        _add_agent_to_swarm_sessions(conn, aid)
    except Exception:
        pass
    return get_agent(conn, aid, set())  # type: ignore[return-value]


def _normalize_workplace_fields(data: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Return ``(primary_id, scope, ids_list)`` from create/update payload."""
    scope = (data.get("workplace_scope") or "single").strip().lower()
    wids = _parse_workplace_ids(data.get("workplace_ids"))
    primary = (data.get("workplace_id") or "").strip()
    if primary == "__all_tunnels__":
        return "", "all_tunnels", []
    if primary == "__all__":
        return "", "all", []
    # Broad scopes only when no concrete primary/list is also assigned.
    if scope in ("all_tunnels", "all-tunnels", "tunnels") and not primary and not wids:
        return "", "all_tunnels", []
    if scope in ("all", "any") and not primary and not wids:
        return "", "all", []
    if wids and not primary:
        primary = wids[0]
    if primary and primary not in wids:
        wids = [primary] + [w for w in wids if w != primary]
    if len(wids) > 1:
        scope = "list"
    elif primary:
        scope = "single"
    else:
        scope = "single"
        wids = []
    return primary, scope, wids


def _add_agent_to_swarm_sessions(conn: sqlite3.Connection, agent_id: str) -> None:
    """Append ``agent_id`` to all sessions that already have 2+ members."""
    rows = conn.execute(
        "SELECT session_id, COUNT(*) AS c FROM session_agents "
        "GROUP BY session_id HAVING c >= 2"
    ).fetchall()
    for row in rows:
        sid = row["session_id"]
        exists = conn.execute(
            "SELECT 1 FROM session_agents WHERE session_id=? AND agent_id=?",
            (sid, agent_id),
        ).fetchone()
        if exists:
            continue
        pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM session_agents WHERE session_id=?",
            (sid,),
        ).fetchone()
        pos = int(pos_row["m"]) + 1 if pos_row else 0
        conn.execute(
            "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
            (sid, agent_id, pos),
        )
    conn.commit()


def update_agent(
    conn: sqlite3.Connection, agent_id: str, data: dict[str, Any], busy_ids: set[str]
) -> dict[str, Any] | None:
    import json

    row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        return None
    sets: list[str] = []
    params: list[Any] = []
    for key in ("name", "description", "model_id", "role"):
        if key in data and data[key] is not None:
            sets.append(f"{key}=?")
            params.append(data[key])
    if any(k in data for k in ("workplace_id", "workplace_ids", "workplace_scope")):
        # Merge with current so partial updates work.
        cur = _row_to_agent(row, busy_ids)
        merged = {
            "workplace_id": data.get("workplace_id", cur.get("workplace_id")),
            "workplace_ids": data.get("workplace_ids", cur.get("workplace_ids")),
            "workplace_scope": data.get("workplace_scope", cur.get("workplace_scope")),
        }
        primary, scope, wids = _normalize_workplace_fields(merged)
        sets.append("workplace_id=?")
        params.append(primary)
        sets.append("workplace_scope=?")
        params.append(scope)
        sets.append("workplace_ids_json=?")
        params.append(json.dumps(wids))
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


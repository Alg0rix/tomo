"""Chat sessions and the session<->agent membership table.

A session row stores ``coordinator_id`` directly; the full ``agent_ids`` list
is reconstructed from the ordered ``session_agents`` rows. The dict returned
to callers keeps the legacy ``agent_id`` (== coordinator) and ``agent_ids``
fields so the API/UI shapes are unchanged.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


def _new_sid() -> str:
    return f"ses_{uuid.uuid4().hex[:8]}"


def session_agent_ids(conn: sqlite3.Connection, session_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT agent_id FROM session_agents WHERE session_id=? ORDER BY position",
        (session_id,),
    ).fetchall()
    return [r["agent_id"] for r in rows]


def _session_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    ids = session_agent_ids(conn, row["id"])
    coord = row["coordinator_id"] or (ids[0] if ids else None)
    return {
        "id": row["id"],
        "agent_id": coord,
        "agent_ids": ids,
        "coordinator_id": coord,
        "user_id": row["user_id"],
        "title": row["title"],
        "message_count": row["message_count"],
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return _session_to_dict(conn, row) if row else None


def list_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    return [_session_to_dict(conn, r) for r in rows]


def _valid_agent_ids(conn: sqlite3.Connection, agent_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for aid in agent_ids:
        if aid in ids:
            continue
        if conn.execute("SELECT 1 FROM agents WHERE id=?", (aid,)).fetchone():
            ids.append(aid)
    return ids


def create_swarm_session(
    conn: sqlite3.Connection,
    agent_ids: list[str],
    user_id: str = "web",
    coordinator_id: str | None = None,
) -> str:
    ids = _valid_agent_ids(conn, agent_ids)
    if not ids:
        raise ValueError("At least one valid agent is required")
    coord = coordinator_id if coordinator_id in ids else ids[0]
    super_row = conn.execute(
        "SELECT id FROM agents WHERE is_super=1 AND id IN (%s)" % ",".join("?" * len(ids)),
        ids,
    ).fetchone()
    if super_row:
        coord = super_row["id"]
    sid = _new_sid()
    now = _now()
    title = "New swarm chat" if len(ids) > 1 else "New conversation"
    conn.execute(
        "INSERT INTO sessions (id, coordinator_id, user_id, title, message_count, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (sid, coord, user_id, title, 0, now, now),
    )
    conn.executemany(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        [(sid, aid, pos) for pos, aid in enumerate(ids)],
    )
    conn.commit()
    return sid


def update_session_agents(
    conn: sqlite3.Connection, session_id: str, agent_ids: list[str]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    ids = _valid_agent_ids(conn, agent_ids)
    if not ids:
        raise ValueError("At least one valid agent is required")
    conn.execute("DELETE FROM session_agents WHERE session_id=?", (session_id,))
    conn.executemany(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        [(session_id, aid, pos) for pos, aid in enumerate(ids)],
    )
    coord = row["coordinator_id"] if row["coordinator_id"] in ids else ids[0]
    conn.execute(
        "UPDATE sessions SET coordinator_id=?, updated_at=? WHERE id=?",
        (coord, _now(), session_id),
    )
    conn.commit()
    return get_session(conn, session_id)


def get_or_create_session(conn: sqlite3.Connection, agent_id: str, user_id: str) -> str:
    """Return the most recent single-agent session for (agent_id, user_id), or create one."""
    row = conn.execute(
        "SELECT s.id FROM sessions s "
        "WHERE s.user_id=? AND s.coordinator_id=? "
        "AND (SELECT COUNT(*) FROM session_agents sa WHERE sa.session_id=s.id)=1 "
        "AND EXISTS (SELECT 1 FROM session_agents sa WHERE sa.session_id=s.id AND sa.agent_id=?) "
        "ORDER BY s.updated_at DESC LIMIT 1",
        (user_id, agent_id, agent_id),
    ).fetchone()
    if row:
        return row["id"]
    sid = _new_sid()
    now = _now()
    conn.execute(
        "INSERT INTO sessions (id, coordinator_id, user_id, title, message_count, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (sid, agent_id, user_id, "New conversation", 0, now, now),
    )
    conn.execute(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        (sid, agent_id, 0),
    )
    conn.commit()
    return sid


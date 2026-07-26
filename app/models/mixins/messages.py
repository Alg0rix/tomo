"""Session message history — CRUD over the ``messages`` table.

Maps the existing ``ChatEntry`` replay shape (type/content/agent_id/function/
params/error/ts) onto the ``messages`` columns. ``params`` is JSON-encoded
into ``params_json``; booleans are stored as INTEGER. Appending a message also
bumps the parent session's ``message_count`` / ``updated_at`` and renames a
fresh "New conversation"/"New swarm chat" session on its first user message.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

_NEW_SESSION_TITLES = ("New conversation", "New swarm chat")


def _now() -> float:
    return time.time()


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["params_json"]) if row["params_json"] else None
    return {
        "type": row["type"],
        "content": row["content"],
        "agent_id": row["agent_id"],
        "function": row["function"],
        "params": params,
        "error": bool(row["error"]),
        "ts": row["ts"],
    }


def get_session_history(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


def append_session_history(conn: sqlite3.Connection, session_id: str, entry: dict[str, Any]) -> None:
    entry.setdefault("ts", _now())
    params = entry.get("params")
    params_json = json.dumps(params) if params is not None else None
    conn.execute(
        "INSERT INTO messages (session_id, type, content, agent_id, function, params_json, error, ts) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            session_id,
            entry.get("type", ""),
            entry.get("content", ""),
            entry.get("agent_id"),
            entry.get("function"),
            params_json,
            1 if entry.get("error") else 0,
            entry["ts"],
        ),
    )
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
    ).fetchone()["c"]
    conn.execute(
        "UPDATE sessions SET message_count=?, updated_at=? WHERE id=?",
        (count, _now(), session_id),
    )
    if entry.get("type") == "user":
        current = conn.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if current and current["title"] in _NEW_SESSION_TITLES:
            title = (entry.get("content") or current["title"])[:60]
            conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
    conn.commit()


def clear_session_history(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    conn.execute(
        "UPDATE sessions SET message_count=0, updated_at=? WHERE id=?",
        (_now(), session_id),
    )
    conn.commit()

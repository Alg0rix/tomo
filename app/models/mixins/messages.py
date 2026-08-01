"""Session message history — CRUD over the ``messages`` table.

Maps the existing ``ChatEntry`` replay shape (type/content/agent_id/function/
params/error/ts) onto the ``messages`` columns. ``params`` is JSON-encoded
into ``params_json``; booleans are stored as INTEGER. Appending a message also
bumps the parent session's ``message_count`` / ``updated_at`` and auto-resolves
a fresh "New conversation"/"New swarm chat" title from the first user message.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

_NEW_SESSION_TITLES = ("New conversation", "New swarm chat")
_TITLE_MAX_LEN = 60


def _now() -> float:
    return time.time()


def derive_session_title(content: str, *, max_len: int = _TITLE_MAX_LEN) -> str:
    """Turn the first user message into a short session title.

    Collapses whitespace, keeps the first line's intent, and truncates on a
    word boundary when longer than ``max_len``.
    """
    text = " ".join((content or "").split()).strip()
    if not text:
        return "New conversation"
    if len(text) <= max_len:
        return text
    cut = text[: max_len + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = text[:max_len]
    return cut.rstrip(".,;:!?…") + "…"


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    params = json.loads(row["params_json"]) if row["params_json"] else None
    entry = {
        "type": row["type"],
        "content": row["content"],
        "agent_id": row["agent_id"],
        "function": row["function"],
        "params": params,
        "error": bool(row["error"]),
        "ts": row["ts"],
    }
    # Surface attachment ids at top level for UI / LLM expansion.
    if isinstance(params, dict) and params.get("attachment_ids"):
        entry["attachment_ids"] = list(params["attachment_ids"])
        if params.get("attachments"):
            entry["attachments"] = params["attachments"]
    return entry


def get_session_history(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


def append_session_history(
    conn: sqlite3.Connection, session_id: str, entry: dict[str, Any]
) -> str | None:
    """Append a history entry. Returns the new session title when auto-resolved."""
    entry.setdefault("ts", _now())
    params = entry.get("params")
    if params is None and entry.get("attachment_ids"):
        params = {
            "attachment_ids": list(entry["attachment_ids"]),
            "attachments": list(entry.get("attachments") or []),
        }
    elif isinstance(params, dict) and entry.get("attachment_ids") and "attachment_ids" not in params:
        params = {
            **params,
            "attachment_ids": list(entry["attachment_ids"]),
            "attachments": list(entry.get("attachments") or params.get("attachments") or []),
        }
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
    msg_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    try:
        from app.runtime.memory.fts import index_message_fts

        index_message_fts(
            conn,
            msg_id=msg_id,
            session_id=session_id,
            msg_type=str(entry.get("type") or ""),
            content=str(entry.get("content") or ""),
        )
    except Exception:
        pass
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (session_id,)
    ).fetchone()["c"]
    conn.execute(
        "UPDATE sessions SET message_count=?, updated_at=? WHERE id=?",
        (count, _now(), session_id),
    )
    resolved: str | None = None
    if entry.get("type") == "user":
        current = conn.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if current and current["title"] in _NEW_SESSION_TITLES:
            resolved = derive_session_title(entry.get("content") or "")
            conn.execute("UPDATE sessions SET title=? WHERE id=?", (resolved, session_id))
    conn.commit()
    return resolved


def clear_session_history(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    try:
        conn.execute("DELETE FROM messages_fts WHERE session_id=?", (session_id,))
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("DELETE FROM session_summaries WHERE session_id=?", (session_id,))
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "UPDATE sessions SET message_count=0, updated_at=? WHERE id=?",
        (_now(), session_id),
    )
    conn.commit()


def search_messages_like(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Keyword LIKE search over message ``content`` across all sessions."""
    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 10), 50))
    pattern = f"%{text}%"
    rows = conn.execute(
        "SELECT session_id, type, content, agent_id, function, ts "
        "FROM messages WHERE content LIKE ? COLLATE NOCASE "
        "ORDER BY ts DESC LIMIT ?",
        (pattern, k),
    ).fetchall()
    return [
        {
            "session_id": r["session_id"],
            "type": r["type"],
            "content": r["content"] or "",
            "agent_id": r["agent_id"],
            "function": r["function"],
            "ts": r["ts"],
        }
        for r in rows
    ]


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Hybrid message search (FTS + LIKE fallback)."""
    from app.runtime.memory.retrieve import search_messages_hybrid

    return search_messages_hybrid(conn, query, limit=limit)

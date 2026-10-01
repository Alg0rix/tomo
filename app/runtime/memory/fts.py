"""FTS5 lexical indexes for knowledge and session messages."""

from __future__ import annotations

import re
import sqlite3


def _fts_query(text: str) -> str:
    """Build a safe FTS5 query from free text (OR of quoted tokens)."""
    tokens = [t for t in re.split(r"[^\w]+", (text or "").lower()) if len(t) > 1]
    if not tokens:
        return ""
    # Quote tokens so OR/AND/NEAR aren't interpreted as operators from user text.
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def rebuild_messages_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM messages_fts")
    except sqlite3.OperationalError:
        return
    rows = conn.execute(
        "SELECT id, session_id, type, content FROM messages "
        "WHERE type IN ('user','final','assistant') AND content != ''"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO messages_fts(msg_id, session_id, type, content) "
            "VALUES (?,?,?,?)",
            (row["id"], row["session_id"], row["type"], row["content"] or ""),
        )


def index_message_fts(
    conn: sqlite3.Connection,
    *,
    msg_id: int,
    session_id: str,
    msg_type: str,
    content: str,
) -> None:
    if msg_type not in {"user", "final", "assistant"} or not (content or "").strip():
        return
    try:
        conn.execute("DELETE FROM messages_fts WHERE msg_id=?", (msg_id,))
        conn.execute(
            "INSERT INTO messages_fts(msg_id, session_id, type, content) "
            "VALUES (?,?,?,?)",
            (msg_id, session_id, msg_type, content),
        )
    except sqlite3.OperationalError:
        pass


def search_messages_fts(
    conn: sqlite3.Connection, query: str, *, limit: int = 10
) -> list[int]:
    q = _fts_query(query)
    if not q:
        return []
    k = max(1, min(int(limit or 10), 50))
    try:
        rows = conn.execute(
            "SELECT msg_id FROM messages_fts WHERE messages_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (q, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [int(r["msg_id"]) for r in rows]


__all__ = [
    "rebuild_messages_fts",
    "index_message_fts",
    "search_messages_fts",
]

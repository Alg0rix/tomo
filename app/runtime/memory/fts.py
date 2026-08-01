"""FTS5 lexical indexes for knowledge and session messages."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


def _fts_query(text: str) -> str:
    """Build a safe FTS5 query from free text (OR of quoted tokens)."""
    tokens = [t for t in re.split(r"[^\w]+", (text or "").lower()) if len(t) > 1]
    if not tokens:
        return ""
    # Quote tokens so OR/AND/NEAR aren't interpreted as operators from user text.
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def rebuild_knowledge_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM knowledge_fts")
    except sqlite3.OperationalError:
        return
    rows = conn.execute(
        "SELECT id, title, body, tags_json FROM knowledge_entries"
    ).fetchall()
    for row in rows:
        tags = row["tags_json"] or ""
        try:
            import json

            parsed = json.loads(tags) if tags else []
            tag_s = " ".join(str(t) for t in parsed) if isinstance(parsed, list) else tags
        except Exception:
            tag_s = tags
        conn.execute(
            "INSERT INTO knowledge_fts(id, title, body, tags) VALUES (?,?,?,?)",
            (row["id"], row["title"] or "", row["body"] or "", tag_s),
        )


def upsert_knowledge_fts(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    eid = entry.get("id") or ""
    if not eid:
        return
    tags = entry.get("tags") or []
    tag_s = " ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
    try:
        conn.execute("DELETE FROM knowledge_fts WHERE id=?", (eid,))
        conn.execute(
            "INSERT INTO knowledge_fts(id, title, body, tags) VALUES (?,?,?,?)",
            (eid, entry.get("title") or "", entry.get("body") or "", tag_s),
        )
    except sqlite3.OperationalError:
        pass


def delete_knowledge_fts(conn: sqlite3.Connection, entry_id: str) -> None:
    try:
        conn.execute("DELETE FROM knowledge_fts WHERE id=?", (entry_id,))
    except sqlite3.OperationalError:
        pass


def search_knowledge_fts(
    conn: sqlite3.Connection, query: str, *, limit: int = 5
) -> list[str]:
    """Return knowledge entry ids ranked by FTS, or [] if FTS unavailable."""
    q = _fts_query(query)
    if not q:
        return []
    k = max(1, min(int(limit or 5), 20))
    try:
        rows = conn.execute(
            "SELECT id FROM knowledge_fts WHERE knowledge_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (q, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r["id"] for r in rows]


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
    "rebuild_knowledge_fts",
    "upsert_knowledge_fts",
    "delete_knowledge_fts",
    "search_knowledge_fts",
    "rebuild_messages_fts",
    "index_message_fts",
    "search_messages_fts",
]

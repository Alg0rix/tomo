"""Session-scoped swarm notes — published when a delegate completes.

Shared memory lane (Learning OS Slice 3). Not NATS — SQLite only.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


def _row_to_note(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"] or "",
        "from_agent_id": row["from_agent_id"] or "",
        "to_agent_id": row["to_agent_id"] or "",
        "delegate_call_id": row["delegate_call_id"] or "",
        "reason": row["reason"] or "",
        "content": row["content"] or "",
        "status": row["status"] or "ok",
        "created_at": float(row["created_at"] or 0),
    }


def insert_swarm_note(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    from_agent_id: str = "",
    to_agent_id: str = "",
    delegate_call_id: str = "",
    reason: str = "",
    content: str = "",
    status: str = "ok",
    note_id: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any] | None:
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "swarm_notes" not in tables:
        return None
    sid = (session_id or "").strip()
    if not sid:
        return None
    eid = (note_id or "").strip() or uuid.uuid4().hex
    ts = float(created_at) if created_at is not None else _now()
    text = (content or "").strip()
    if len(text) > 8000:
        text = text[:7997] + "…"
    conn.execute(
        """
        INSERT INTO swarm_notes (
            id, session_id, from_agent_id, to_agent_id, delegate_call_id,
            reason, content, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            sid,
            (from_agent_id or "").strip(),
            (to_agent_id or "").strip(),
            (delegate_call_id or "").strip(),
            (reason or "").strip()[:500],
            text,
            (status or "ok").strip() or "ok",
            ts,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM swarm_notes WHERE id=?", (eid,)).fetchone()
    return _row_to_note(row) if row else None


def list_swarm_notes(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    sid = (session_id or "").strip()
    if not sid:
        return []
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "swarm_notes" not in tables:
        return []
    lim = max(1, min(int(limit or 20), 100))
    rows = conn.execute(
        """
        SELECT * FROM swarm_notes
        WHERE session_id=?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (sid, lim),
    ).fetchall()
    return [_row_to_note(r) for r in rows]


def format_swarm_notes_snippet(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    limit: int = 6,
    max_chars: int = 1200,
) -> str:
    notes = list_swarm_notes(conn, session_id=session_id, limit=limit)
    if not notes:
        return ""
    lines: list[str] = []
    for n in notes:
        who = n["to_agent_id"] or "?"
        frm = n["from_agent_id"] or "?"
        status = n["status"]
        reason = (n["reason"] or "")[:80]
        body = (n["content"] or "").replace("\n", " ").strip()[:180]
        mark = "✗" if status == "error" else "✓"
        lines.append(f"- {mark} {frm}→{who}: {reason} — {body}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 12] + "\n…(truncated)"
    return text


__all__ = [
    "insert_swarm_note",
    "list_swarm_notes",
    "format_swarm_notes_snippet",
]

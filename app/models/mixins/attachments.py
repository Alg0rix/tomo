"""Attachment persistence — files linked to a session."""

from __future__ import annotations

import sqlite3
import time
from typing import Any


def _now() -> float:
    return time.time()


def create_attachment(
    conn: sqlite3.Connection,
    attachment_id: str,
    session_id: str,
    filename: str,
    original_name: str,
    mime_type: str,
    size_bytes: int,
    file_path: str,
) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO attachments (id, session_id, filename, original_name, mime_type, size_bytes, file_path, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (attachment_id, session_id, filename, original_name, mime_type, size_bytes, file_path, _now()),
    )
    conn.commit()
    return get_attachment(conn, attachment_id)


def get_attachment(conn: sqlite3.Connection, attachment_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "file_path": row["file_path"],
        "created_at": row["created_at"],
    }


def list_session_attachments(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM attachments WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "filename": r["filename"],
            "original_name": r["original_name"],
            "mime_type": r["mime_type"],
            "size_bytes": r["size_bytes"],
            "file_path": r["file_path"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def delete_attachment(conn: sqlite3.Connection, attachment_id: str) -> bool:
    cur = conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
    conn.commit()
    return cur.rowcount > 0

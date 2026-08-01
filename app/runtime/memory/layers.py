"""Memory layers: agent state, artifacts, session summaries."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


# ── Agent state ──────────────────────────────────────────────────────

def list_agent_state(conn: sqlite3.Connection, agent_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM agent_state WHERE agent_id=? ORDER BY key",
        (agent_id,),
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_agent_state(conn: sqlite3.Connection, agent_id: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM agent_state WHERE agent_id=? AND key=?",
        (agent_id, key),
    ).fetchone()
    return row["value"] if row else None


def set_agent_state(
    conn: sqlite3.Connection, agent_id: str, key: str, value: str
) -> None:
    key = (key or "").strip()
    if not agent_id or not key:
        raise ValueError("agent_id and key are required")
    conn.execute(
        "INSERT INTO agent_state(agent_id, key, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (agent_id, key, value or "", _now()),
    )
    conn.commit()


def delete_agent_state(conn: sqlite3.Connection, agent_id: str, key: str) -> bool:
    cur = conn.execute(
        "DELETE FROM agent_state WHERE agent_id=? AND key=?",
        (agent_id, key),
    )
    conn.commit()
    return cur.rowcount > 0


# ── Artifacts ────────────────────────────────────────────────────────

def _artifact_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {
        "id": row["id"],
        "title": row["title"],
        "path": row["path"],
        "kind": row["kind"],
        "session_id": row["session_id"],
        "agent_id": row["agent_id"],
        "notes": row["notes"],
        "meta": meta if isinstance(meta, dict) else {},
        "created_at": row["created_at"],
    }


def create_artifact(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    aid = (data.get("id") or "").strip() or f"art_{uuid.uuid4().hex[:12]}"
    now = _now()
    conn.execute(
        "INSERT INTO artifacts(id, title, path, kind, session_id, agent_id, notes, "
        "meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            aid,
            title,
            (data.get("path") or "").strip(),
            (data.get("kind") or "file").strip() or "file",
            (data.get("session_id") or "").strip(),
            (data.get("agent_id") or "").strip(),
            (data.get("notes") or "").strip(),
            json.dumps(data.get("meta") or {}, ensure_ascii=False),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
    return _artifact_row(row)


def search_artifacts(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 5,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    text = (query or "").strip().lower()
    k = max(1, min(int(limit or 5), 20))
    sid = (session_id or "").strip()
    if sid:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT 200",
            (sid,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    if not text:
        return [_artifact_row(r) for r in rows[:k]]
    tokens = [t for t in text.split() if t]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        art = _artifact_row(row)
        hay = f"{art['title']} {art['path']} {art['notes']} {art['kind']}".lower()
        score = sum(1 for t in tokens if t in hay)
        if score:
            scored.append((score, art))
    scored.sort(key=lambda p: (-p[0], -p[1]["created_at"]))
    return [a for _, a in scored[:k]]


def list_artifacts(
    conn: sqlite3.Connection, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?",
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [_artifact_row(r) for r in rows]


# ── Session summaries ────────────────────────────────────────────────

def get_session_summary(
    conn: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM session_summaries WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row:
        return None
    return {
        "session_id": row["session_id"],
        "summary": row["summary"],
        "message_count": int(row["message_count"] or 0),
        "updated_at": row["updated_at"],
    }


def upsert_session_summary(
    conn: sqlite3.Connection,
    session_id: str,
    summary: str,
    *,
    message_count: int = 0,
) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO session_summaries(session_id, summary, message_count, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
        "summary=excluded.summary, message_count=excluded.message_count, "
        "updated_at=excluded.updated_at",
        (session_id, (summary or "").strip(), int(message_count or 0), _now()),
    )
    conn.commit()
    return get_session_summary(conn, session_id)  # type: ignore[return-value]


__all__ = [
    "list_agent_state",
    "get_agent_state",
    "set_agent_state",
    "delete_agent_state",
    "create_artifact",
    "search_artifacts",
    "list_artifacts",
    "get_session_summary",
    "upsert_session_summary",
]

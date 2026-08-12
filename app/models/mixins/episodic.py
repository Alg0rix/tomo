"""Episodic memory — concrete past experiences (per login account).

Example: tried deploy X on project Y, hit error Z, fixed with A, succeeded.

Distinct from the learning **diary** (``learning_events.diary``), which is only
a short growth-log line for Companion.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


def _row_to_episode(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": (row["user_id"] or "web").strip() or "web",
        "session_id": row["session_id"] or "",
        "agent_id": row["agent_id"] or "",
        "title": row["title"] or "",
        "tried": row["tried"] or "",
        "context": row["context"] or "",
        "error": row["error"] or "",
        "fix": row["fix"] or "",
        "outcome": row["outcome"] or "",
        "summary": row["summary"] or "",
        "created_at": float(row["created_at"] or 0),
    }


def _build_summary(
    *,
    title: str,
    tried: str,
    context: str,
    error: str,
    fix: str,
    outcome: str,
) -> str:
    """Canonical narrative used for search and retrieve snippets."""
    if (title or "").strip() and not any(
        (tried, context, error, fix, outcome)
    ):
        return title.strip()
    parts: list[str] = []
    if tried.strip():
        parts.append(f"Tried: {tried.strip()}")
    if context.strip():
        parts.append(f"Context: {context.strip()}")
    if error.strip():
        parts.append(f"Error: {error.strip()}")
    if fix.strip():
        parts.append(f"Fix: {fix.strip()}")
    if outcome.strip():
        parts.append(f"Outcome: {outcome.strip()}")
    body = ". ".join(parts)
    if title.strip() and body:
        return f"{title.strip()} — {body}"
    return title.strip() or body


def insert_episode(
    conn: sqlite3.Connection,
    *,
    user_id: str = "web",
    session_id: str = "",
    agent_id: str = "",
    title: str = "",
    tried: str = "",
    context: str = "",
    error: str = "",
    fix: str = "",
    outcome: str = "",
    summary: str = "",
    episode_id: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any] | None:
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "episodic_memories" not in tables:
        return None

    tit = (title or "").strip()
    tr = (tried or "").strip()
    ctx = (context or "").strip()
    err = (error or "").strip()
    fx = (fix or "").strip()
    outc = (outcome or "").strip()
    summ = (summary or "").strip() or _build_summary(
        title=tit, tried=tr, context=ctx, error=err, fix=fx, outcome=outc
    )
    if not summ and not tit and not tr:
        return None

    eid = (episode_id or "").strip() or f"ep_{uuid.uuid4().hex[:12]}"
    ts = float(created_at) if created_at is not None else _now()
    uid = (user_id or "web").strip() or "web"
    conn.execute(
        """
        INSERT INTO episodic_memories (
            id, user_id, session_id, agent_id, title, tried, context,
            error, fix, outcome, summary, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            eid,
            uid,
            (session_id or "").strip(),
            (agent_id or "").strip(),
            tit[:240],
            tr[:2000],
            ctx[:2000],
            err[:2000],
            fx[:2000],
            outc[:1000],
            summ[:4000],
            ts,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM episodic_memories WHERE id=?", (eid,)
    ).fetchone()
    return _row_to_episode(row) if row else None


def get_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    eid = (episode_id or "").strip()
    if not eid:
        return None
    uid = (user_id or "").strip()
    if uid:
        row = conn.execute(
            "SELECT * FROM episodic_memories WHERE id=? AND user_id=?",
            (eid, uid),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM episodic_memories WHERE id=?", (eid,)
        ).fetchone()
    return _row_to_episode(row) if row else None


def list_episodes(
    conn: sqlite3.Connection,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 20), 100))
    clauses: list[str] = []
    params: list[Any] = []
    uid = (user_id or "").strip()
    if uid:
        clauses.append("user_id=?")
        params.append(uid)
    sid = (session_id or "").strip()
    if sid:
        clauses.append("session_id=?")
        params.append(sid)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(lim)
    rows = conn.execute(
        f"""
        SELECT * FROM episodic_memories
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_episode(r) for r in rows]


def search_episodes(
    conn: sqlite3.Connection,
    query: str,
    *,
    user_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Token overlap search over title/summary/tried/error/fix (per user)."""
    text = (query or "").strip().lower()
    if not text:
        return []
    tokens = [t for t in re.split(r"\s+", text) if len(t) > 1]
    if not tokens:
        return []
    # Pull a bounded candidate set for this user.
    candidates = list_episodes(conn, user_id=user_id, limit=200)
    scored: list[tuple[int, dict[str, Any]]] = []
    for ep in candidates:
        hay = " ".join(
            [
                ep.get("title") or "",
                ep.get("summary") or "",
                ep.get("tried") or "",
                ep.get("context") or "",
                ep.get("error") or "",
                ep.get("fix") or "",
                ep.get("outcome") or "",
            ]
        ).lower()
        score = 0
        for tok in tokens:
            if tok in hay:
                score += 1
                if tok in (ep.get("title") or "").lower():
                    score += 1
                if tok in (ep.get("error") or "").lower():
                    score += 1
        if score > 0:
            scored.append((score, ep))
    scored.sort(key=lambda p: (-p[0], -(p[1].get("created_at") or 0)))
    return [e for _, e in scored[: max(1, min(int(limit or 5), 20))]]


__all__ = [
    "insert_episode",
    "get_episode",
    "list_episodes",
    "search_episodes",
]

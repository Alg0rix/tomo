"""Queryable index of execution-lane snippets (artifacts + review tags).

Learning OS Slice 3 — not a full FTS corpus; lightweight SQLite rows for
retrieve / Companion hints.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [str(t).strip() for t in val if str(t).strip()]
        except json.JSONDecodeError:
            pass
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _row_to_snip(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"] or "",
        "agent_id": row["agent_id"] or "",
        "source": row["source"] or "review",
        "ref_id": row["ref_id"] or "",
        "title": row["title"] or "",
        "snippet": row["snippet"] or "",
        "tags": _parse_tags(row["tags_json"]),
        "created_at": float(row["created_at"] or 0),
    }


def insert_execution_snippet(
    conn: sqlite3.Connection,
    *,
    session_id: str = "",
    agent_id: str = "",
    source: str = "review",
    ref_id: str = "",
    title: str = "",
    snippet: str = "",
    tags: list[str] | None = None,
    snippet_id: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any] | None:
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "execution_snippets" not in tables:
        return None
    text = (snippet or "").strip()
    tit = (title or "").strip()
    if not text and not tit:
        return None
    if len(text) > 4000:
        text = text[:3997] + "…"
    eid = (snippet_id or "").strip() or uuid.uuid4().hex
    ts = float(created_at) if created_at is not None else _now()
    conn.execute(
        """
        INSERT INTO execution_snippets (
            id, session_id, agent_id, source, ref_id, title, snippet, tags_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            (session_id or "").strip(),
            (agent_id or "").strip(),
            (source or "review").strip() or "review",
            (ref_id or "").strip(),
            tit[:240],
            text,
            json.dumps(_parse_tags(tags), ensure_ascii=False),
            ts,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM execution_snippets WHERE id=?", (eid,)
    ).fetchone()
    return _row_to_snip(row) if row else None


def list_execution_snippets(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "execution_snippets" not in tables:
        return []
    lim = max(1, min(int(limit or 20), 100))
    sid = (session_id or "").strip()
    if sid:
        rows = conn.execute(
            """
            SELECT * FROM execution_snippets
            WHERE session_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (sid, lim),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM execution_snippets
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [_row_to_snip(r) for r in rows]


def search_execution_snippets(
    conn: sqlite3.Connection,
    query: str,
    *,
    session_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    text = (query or "").strip().lower()
    if not text:
        return []
    tokens = [t for t in re.split(r"\s+", text) if len(t) > 1]
    if not tokens:
        return []
    candidates = list_execution_snippets(
        conn, session_id=session_id, limit=max(50, limit * 10)
    )
    scored: list[tuple[int, dict[str, Any]]] = []
    for snip in candidates:
        hay = " ".join(
            [
                snip.get("title", "").lower(),
                snip.get("snippet", "").lower(),
                " ".join(t.lower() for t in snip.get("tags") or []),
            ]
        )
        score = sum(1 for tok in tokens if tok in hay)
        if score:
            scored.append((score, snip))
    scored.sort(key=lambda p: (-p[0], -p[1]["created_at"]))
    return [s for _, s in scored[: max(1, min(int(limit or 5), 20))]]


def index_from_review_extract(
    conn: sqlite3.Connection,
    extract: dict[str, Any] | None,
    *,
    session_id: str = "",
    agent_id: str = "",
) -> int:
    """Insert snippets for saved-eligible execution items. Returns count."""
    if not isinstance(extract, dict):
        return 0
    items = extract.get("items") or []
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "execution":
            continue
        if not item.get("saved_eligible"):
            continue
        summary = str(item.get("summary") or "").strip()
        tool = str(item.get("tool") or "execution")
        if not summary:
            continue
        insert_execution_snippet(
            conn,
            session_id=session_id,
            agent_id=agent_id,
            source="review",
            ref_id=tool,
            title=f"{tool} outcome",
            snippet=summary,
            tags=["execution", tool],
        )
        n += 1
    return n


__all__ = [
    "insert_execution_snippet",
    "list_execution_snippets",
    "search_execution_snippets",
    "index_from_review_extract",
]

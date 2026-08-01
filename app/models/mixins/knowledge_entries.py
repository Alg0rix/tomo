"""Knowledge entries — SQLite CRUD + hybrid search (FTS + semantic).

Stores title/body/tags rows used by the ``recall`` / ``remember`` tools and
the System → Memory UI.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any

_logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _now() -> float:
    return time.time()


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except json.JSONDecodeError:
            pass
        return [p.strip() for p in text.split(",") if p.strip()]
    return []


def _tags_json(tags: Any) -> str:
    return json.dumps(_parse_tags(tags), ensure_ascii=False)


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "tags": _parse_tags(row["tags_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _index_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    try:
        from app.runtime.memory.fts import upsert_knowledge_fts

        upsert_knowledge_fts(conn, entry)
    except Exception as exc:
        _logger.debug("knowledge fts index failed: %s", exc)
    try:
        from app.runtime.memory.embeddings import upsert_embedding

        blob = f"{entry.get('title') or ''}\n{entry.get('body') or ''}"
        upsert_embedding(conn, scope="knowledge", ref_id=entry["id"], text=blob)
    except Exception as exc:
        _logger.debug("knowledge embed failed: %s", exc)


def _drop_index(conn: sqlite3.Connection, entry_id: str) -> None:
    try:
        from app.runtime.memory.fts import delete_knowledge_fts

        delete_knowledge_fts(conn, entry_id)
    except Exception:
        pass
    try:
        from app.runtime.memory.embeddings import delete_embedding

        delete_embedding(conn, scope="knowledge", ref_id=entry_id)
    except Exception:
        pass


def list_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM knowledge_entries ORDER BY updated_at DESC, created_at DESC"
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


def get_entry(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)
    ).fetchone()
    return _row_to_entry(row) if row else None


def create_entry(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    from app.models.ids import unique_id

    eid = unique_id(
        conn,
        "knowledge_entries",
        name=title,
        prefix="kb",
        explicit=(data.get("id") or None) or None,
    )
    now = _now()
    conn.execute(
        "INSERT INTO knowledge_entries (id, title, body, tags_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            eid,
            title,
            (data.get("body") or "").strip(),
            _tags_json(data.get("tags")),
            now,
            now,
        ),
    )
    conn.commit()
    entry = get_entry(conn, eid)
    assert entry is not None
    _index_entry(conn, entry)
    conn.commit()
    return entry


def update_entry(
    conn: sqlite3.Connection, entry_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    if not conn.execute(
        "SELECT 1 FROM knowledge_entries WHERE id=?", (entry_id,)
    ).fetchone():
        return None
    sets: list[str] = []
    params: list[Any] = []
    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title:
            raise ValueError("title must not be empty")
        sets.append("title=?")
        params.append(title)
    if "body" in data and data["body"] is not None:
        sets.append("body=?")
        params.append(str(data["body"]).strip())
    if "tags" in data and data["tags"] is not None:
        sets.append("tags_json=?")
        params.append(_tags_json(data["tags"]))
    if sets:
        sets.append("updated_at=?")
        params.append(_now())
        params.append(entry_id)
        conn.execute(
            f"UPDATE knowledge_entries SET {', '.join(sets)} WHERE id=?",
            params,
        )
        conn.commit()
    entry = get_entry(conn, entry_id)
    if entry:
        _index_entry(conn, entry)
        conn.commit()
    return entry


def delete_entry(conn: sqlite3.Connection, entry_id: str) -> bool:
    if not conn.execute(
        "SELECT 1 FROM knowledge_entries WHERE id=?", (entry_id,)
    ).fetchone():
        return False
    conn.execute("DELETE FROM knowledge_entries WHERE id=?", (entry_id,))
    _drop_index(conn, entry_id)
    conn.commit()
    return True


def search_entries_lexical(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Token-overlap scorer (fallback when FTS/semantic miss)."""
    text = (query or "").strip()
    if not text:
        return []
    k = max(1, min(int(limit or 5), 20))
    tokens = [t for t in re.split(r"\s+", text.lower()) if t]
    if not tokens:
        return []

    rows = conn.execute("SELECT * FROM knowledge_entries").fetchall()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        entry = _row_to_entry(row)
        hay = " ".join(
            [
                entry["title"].lower(),
                entry["body"].lower(),
                " ".join(t.lower() for t in entry["tags"]),
            ]
        )
        score = 0
        for tok in tokens:
            if tok in hay:
                score += 1
                if tok in entry["title"].lower():
                    score += 1
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda pair: (-pair[0], -pair[1]["updated_at"]))
    return [e for _, e in scored[:k]]


def search_entries(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Hybrid search (FTS + semantic + lexical fallback)."""
    from app.runtime.memory.retrieve import search_knowledge_hybrid

    return search_knowledge_hybrid(conn, query, limit=limit)


__all__ = [
    "list_entries",
    "get_entry",
    "create_entry",
    "update_entry",
    "delete_entry",
    "search_entries",
    "search_entries_lexical",
]

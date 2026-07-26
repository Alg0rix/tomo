"""Auto-generate stable slug ids for create endpoints.

Users supply a human name/title; the server derives a unique ``[a-z0-9_]+`` id.
Optional explicit ids from API/seed are still accepted when provided.
"""

from __future__ import annotations

import re
import sqlite3
import uuid

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
# Allow single-char ids for tests/seeds; API create still prefers 2+ via schema.
_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def slugify(text: str, *, max_len: int = 40, fallback: str = "item") -> str:
    s = _SLUG_RE.sub("_", (text or "").strip().lower()).strip("_")
    s = s[:max_len].strip("_")
    return s or fallback


def is_valid_id(value: str | None) -> bool:
    return bool(value and _ID_RE.match(value))


def unique_id(
    conn: sqlite3.Connection,
    table: str,
    *,
    name: str = "",
    prefix: str = "",
    explicit: str | None = None,
    max_len: int = 40,
) -> str:
    """Return a unique primary key for ``table``.

    * If ``explicit`` is a valid free id, use it (seed / API override).
    * Else slugify ``name`` with optional ``prefix_`` and suffix on collision.
    """
    if is_valid_id(explicit):
        taken = conn.execute(
            f"SELECT 1 FROM {table} WHERE id=?", (explicit,)
        ).fetchone()
        if taken:
            raise ValueError(f"ID already exists: {explicit}")
        return explicit  # type: ignore[return-value]

    base = slugify(name, max_len=max_len, fallback=prefix.strip("_") or "item")
    if prefix:
        p = prefix if prefix.endswith("_") else f"{prefix}_"
        if not base.startswith(p):
            base = f"{p}{base}"[: 64 - 4]
    base = base[:56] or (prefix.rstrip("_") or "item")
    candidate = base
    n = 0
    while conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (candidate,)).fetchone():
        n += 1
        candidate = f"{base}_{n}"
        if n > 50:
            return f"{prefix.rstrip('_') or 'id'}_{uuid.uuid4().hex[:12]}"
    return candidate


__all__ = ["slugify", "is_valid_id", "unique_id"]

"""Companion read model — bond, growth, profile over the learning ledger.

Owns SQL aggregates that are *not* ledger CRUD (user messages, active days).
Ledger insert/list stay in ``app.models.mixins.learning_events``.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from app.models.mixins import learning_events as le
from app.models.mixins import sessions as sessions_store
from app.models.mixins import skills as skills_store
from app.models.mixins import settings as settings_store
from app.runtime.agent.learning.bond import compute_bond


def _days_together(first_seen_at: float | None) -> int:
    if not first_seen_at or first_seen_at <= 0:
        return 0
    now = datetime.now(timezone.utc).date()
    start = datetime.fromtimestamp(float(first_seen_at), tz=timezone.utc).date()
    return max(0, (now - start).days)


def _user_profile() -> tuple[list[str], int, int]:
    """Return (preview entries, total entry count, char count) from one read."""
    try:
        from app.runtime.memory import curated

        entries = curated.read_entries(curated.user_path())
        cleaned = [e.strip() for e in entries if (e or "").strip()]
        chars = len(curated.ENTRY_DELIMITER.join(cleaned)) if cleaned else 0
        return cleaned[:12], len(cleaned), chars
    except Exception:
        return [], 0, 0


def count_user_messages(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE type='user'"
    ).fetchone()
    return int(row["c"] if row else 0)


def first_activity_at(conn: sqlite3.Connection) -> float | None:
    """Earliest real activity: first user message ts, else session/learning."""
    candidates: list[float] = []
    row = conn.execute(
        "SELECT MIN(ts) AS t FROM messages WHERE type='user' AND ts > 0"
    ).fetchone()
    if row and row["t"]:
        candidates.append(float(row["t"]))
    row = conn.execute(
        "SELECT MIN(created_at) AS t FROM sessions WHERE created_at > 0"
    ).fetchone()
    if row and row["t"]:
        candidates.append(float(row["t"]))
    row = conn.execute(
        "SELECT MIN(created_at) AS t FROM learning_events WHERE created_at > 0"
    ).fetchone()
    if row and row["t"]:
        candidates.append(float(row["t"]))
    return min(candidates) if candidates else None


def distinct_active_days(conn: sqlite3.Connection) -> int:
    """Distinct UTC days with a user message *or* a learning event.

    Uses message ``ts`` and learning_events ``created_at`` (not session proxies).
    """
    days: set[str] = set()
    for row in conn.execute(
        "SELECT ts FROM messages WHERE type='user' AND ts > 0"
    ):
        dt = datetime.fromtimestamp(float(row["ts"]), tz=timezone.utc)
        days.add(dt.strftime("%Y-%m-%d"))
    for row in conn.execute(
        "SELECT created_at FROM learning_events WHERE created_at > 0"
    ):
        dt = datetime.fromtimestamp(float(row["created_at"]), tz=timezone.utc)
        days.add(dt.strftime("%Y-%m-%d"))
    return len(days)


def session_user_id(conn: sqlite3.Connection, session_id: str | None) -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "web"
    sess = sessions_store.get_session(conn, sid)
    if not sess:
        return "web"
    return (sess.get("user_id") or "web").strip() or "web"


def companion_snapshot(conn: sqlite3.Connection, *, recent_limit: int = 20) -> dict[str, Any]:
    """Full payload for GET /api/companion (single connection)."""
    settings = settings_store.get_settings(conn)
    learning_on = bool(settings.get("learning_enabled", True))

    stats_ev = le.learning_event_stats(conn)
    growth = le.learning_events_by_month(conn, months=12)
    recent = le.list_learning_events(conn, limit=recent_limit)

    chats = count_user_messages(conn)
    first_seen = first_activity_at(conn)
    days_active = distinct_active_days(conn)
    try:
        library_skills = len(skills_store.list_skills(conn) or [])
    except Exception:
        library_skills = 0

    preview, user_entry_count, user_chars = _user_profile()

    parts = {
        "chats": chats,
        "saved_events": int(stats_ev.get("events_saved") or 0),
        "user_memory_chars": user_chars,
        "library_skills": library_skills,
        "days_active": days_active,
    }
    bond = compute_bond(**parts)

    return {
        "bond": bond,
        "bond_parts": parts,
        "days_together": _days_together(first_seen),
        "first_seen_at": first_seen,
        "learning_enabled": learning_on,
        "stats": {
            **stats_ev,
            "skills_library": library_skills,
            "user_entries": user_entry_count,
        },
        "growth": growth,
        "recent_events": recent,
        "user_profile_preview": preview,
        "generated_at": time.time(),
    }


__all__ = [
    "count_user_messages",
    "first_activity_at",
    "distinct_active_days",
    "session_user_id",
    "companion_snapshot",
]

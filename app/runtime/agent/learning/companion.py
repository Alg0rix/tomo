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


def _activity_day_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Per UTC day: chats (user msgs) and saves (learning_events.saved=1)."""
    days: dict[str, dict[str, int]] = {}

    def bucket(key: str) -> dict[str, int]:
        if key not in days:
            days[key] = {"chats": 0, "saves": 0, "reviews": 0}
        return days[key]

    for row in conn.execute(
        "SELECT ts FROM messages WHERE type='user' AND ts > 0"
    ):
        key = datetime.fromtimestamp(float(row["ts"]), tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        bucket(key)["chats"] += 1
    for row in conn.execute(
        "SELECT created_at, saved FROM learning_events WHERE created_at > 0"
    ):
        key = datetime.fromtimestamp(
            float(row["created_at"]), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        b = bucket(key)
        b["reviews"] += 1
        if row["saved"]:
            b["saves"] += 1
    return days


def activity_heatmap(
    conn: sqlite3.Connection, *, weeks: int = 26
) -> dict[str, Any]:
    """GitHub-style day grid for the last ``weeks`` (Mon-start columns)."""
    from datetime import date, timedelta

    n_weeks = max(4, min(int(weeks or 26), 52))
    today = datetime.now(timezone.utc).date()
    end = today
    start = end - timedelta(days=end.weekday()) - timedelta(weeks=n_weeks - 1)
    counts = _activity_day_counts(conn)

    days_out: list[dict[str, Any]] = []
    cur = start
    while cur <= end:
        key = cur.isoformat()
        c = counts.get(key) or {"chats": 0, "saves": 0, "reviews": 0}
        # Intensity for bond-oriented view: chats + 2*saves + reviews
        intensity = int(c["chats"]) + 2 * int(c["saves"]) + int(c["reviews"])
        days_out.append(
            {
                "date": key,
                "weekday": cur.weekday(),  # 0=Mon
                "chats": int(c["chats"]),
                "saves": int(c["saves"]),
                "reviews": int(c["reviews"]),
                "intensity": intensity,
            }
        )
        cur += timedelta(days=1)

    # Month labels for columns (first day of each month appearing)
    months: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, d in enumerate(days_out):
        ym = d["date"][:7]
        if ym in seen:
            continue
        if date.fromisoformat(d["date"]).day > 7 and i > 0:
            continue
        seen.add(ym)
        months.append({"month": ym, "index": i})

    # Current streak: consecutive days ending today/yesterday with intensity>0
    streak = 0
    check = today
    active_keys = {d["date"] for d in days_out if d["intensity"] > 0}
    # Allow streak to continue if today is empty but yesterday had activity
    if check.isoformat() not in active_keys:
        check = today - timedelta(days=1)
    while check.isoformat() in active_keys:
        streak += 1
        check -= timedelta(days=1)

    return {
        "weeks": n_weeks,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days_out,
        "months": months,
        "streak": streak,
        "max_intensity": max((d["intensity"] for d in days_out), default=0),
    }


def companion_snapshot(conn: sqlite3.Connection, *, recent_limit: int = 20) -> dict[str, Any]:
    """Full payload for GET /api/companion (single connection)."""
    settings = settings_store.get_settings(conn)
    learning_on = bool(settings.get("learning_enabled", True))

    stats_ev = le.learning_event_stats(conn)
    growth = le.learning_events_by_month(conn, months=12)
    recent = le.list_learning_events(conn, limit=recent_limit)
    heatmap = activity_heatmap(conn, weeks=26)

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
        "streak": int(heatmap.get("streak") or 0),
        "stats": {
            **stats_ev,
            "skills_library": library_skills,
            "user_entries": user_entry_count,
        },
        "growth": growth,
        "heatmap": heatmap,
        "recent_events": recent,
        "user_profile_preview": preview,
        "generated_at": time.time(),
    }


__all__ = [
    "count_user_messages",
    "first_activity_at",
    "distinct_active_days",
    "session_user_id",
    "activity_heatmap",
    "companion_snapshot",
]

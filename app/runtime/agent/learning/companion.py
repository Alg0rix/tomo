"""Companion read model — bond, growth, profile over the learning ledger.

Owns SQL aggregates that are *not* ledger CRUD (user messages, active days).
Ledger insert/list stay in ``app.models.mixins.learning_events``.

Multi-user: all aggregates and previews are scoped by ``user_id`` (login account).
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


def _user_profile(user_id: str | None = None) -> tuple[list[str], int, int]:
    """Return (preview entries, total entry count, char count) from one read."""
    try:
        from app.runtime.memory import curated

        entries = curated.read_user_entries(user_id=user_id)
        cleaned = [e.strip() for e in entries if (e or "").strip()]
        chars = len(curated.ENTRY_DELIMITER.join(cleaned)) if cleaned else 0
        return cleaned[:12], len(cleaned), chars
    except Exception:
        return [], 0, 0


def count_user_messages(
    conn: sqlite3.Connection, *, user_id: str | None = None
) -> int:
    uid = (user_id or "").strip()
    if uid:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.type='user' AND s.user_id=?",
            (uid,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE type='user'"
        ).fetchone()
    return int(row["c"] if row else 0)


def first_activity_at(
    conn: sqlite3.Connection, *, user_id: str | None = None
) -> float | None:
    """Earliest real activity for this account (or global when user_id omitted)."""
    candidates: list[float] = []
    uid = (user_id or "").strip()
    if uid:
        row = conn.execute(
            "SELECT MIN(m.ts) AS t FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.type='user' AND m.ts > 0 AND s.user_id=?",
            (uid,),
        ).fetchone()
        if row and row["t"]:
            candidates.append(float(row["t"]))
        row = conn.execute(
            "SELECT MIN(created_at) AS t FROM sessions "
            "WHERE created_at > 0 AND user_id=?",
            (uid,),
        ).fetchone()
        if row and row["t"]:
            candidates.append(float(row["t"]))
        row = conn.execute(
            "SELECT MIN(created_at) AS t FROM learning_events "
            "WHERE created_at > 0 AND user_id=?",
            (uid,),
        ).fetchone()
        if row and row["t"]:
            candidates.append(float(row["t"]))
    else:
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


def distinct_active_days(
    conn: sqlite3.Connection, *, user_id: str | None = None
) -> int:
    """Distinct UTC days with a user message *or* a learning event."""
    days: set[str] = set()
    uid = (user_id or "").strip()
    if uid:
        for row in conn.execute(
            "SELECT m.ts FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.type='user' AND m.ts > 0 AND s.user_id=?",
            (uid,),
        ):
            dt = datetime.fromtimestamp(float(row["ts"]), tz=timezone.utc)
            days.add(dt.strftime("%Y-%m-%d"))
        for row in conn.execute(
            "SELECT created_at FROM learning_events "
            "WHERE created_at > 0 AND user_id=?",
            (uid,),
        ):
            dt = datetime.fromtimestamp(float(row["created_at"]), tz=timezone.utc)
            days.add(dt.strftime("%Y-%m-%d"))
    else:
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


def _activity_day_counts(
    conn: sqlite3.Connection, *, user_id: str | None = None
) -> dict[str, dict[str, int]]:
    """Per UTC day: chats (user msgs) and saves (learning_events.saved=1)."""
    days: dict[str, dict[str, int]] = {}

    def bucket(key: str) -> dict[str, int]:
        if key not in days:
            days[key] = {"chats": 0, "saves": 0, "reviews": 0}
        return days[key]

    uid = (user_id or "").strip()
    if uid:
        for row in conn.execute(
            "SELECT m.ts FROM messages m "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE m.type='user' AND m.ts > 0 AND s.user_id=?",
            (uid,),
        ):
            key = datetime.fromtimestamp(float(row["ts"]), tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
            bucket(key)["chats"] += 1
        for row in conn.execute(
            "SELECT created_at, saved FROM learning_events "
            "WHERE created_at > 0 AND user_id=?",
            (uid,),
        ):
            key = datetime.fromtimestamp(
                float(row["created_at"]), tz=timezone.utc
            ).strftime("%Y-%m-%d")
            b = bucket(key)
            b["reviews"] += 1
            if row["saved"]:
                b["saves"] += 1
    else:
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
    conn: sqlite3.Connection, *, weeks: int = 26, user_id: str | None = None
) -> dict[str, Any]:
    """GitHub-style day grid for the last ``weeks`` (Mon-start columns)."""
    from datetime import date, timedelta

    n_weeks = max(4, min(int(weeks or 26), 52))
    today = datetime.now(timezone.utc).date()
    end = today
    start = end - timedelta(days=end.weekday()) - timedelta(weeks=n_weeks - 1)
    counts = _activity_day_counts(conn, user_id=user_id)

    days_out: list[dict[str, Any]] = []
    cur = start
    while cur <= end:
        key = cur.isoformat()
        c = counts.get(key) or {"chats": 0, "saves": 0, "reviews": 0}
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

    streak = 0
    check = today
    active_keys = {d["date"] for d in days_out if d["intensity"] > 0}
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


def companion_snapshot(
    conn: sqlite3.Connection,
    *,
    recent_limit: int = 20,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Full payload for GET /api/companion (single connection, per account)."""
    settings = settings_store.get_settings(conn)
    learning_on = bool(settings.get("learning_enabled", True))
    uid = (user_id or "").strip() or None

    stats_ev = le.learning_event_stats(conn, user_id=uid)
    growth = le.learning_events_by_month(conn, months=12, user_id=uid)
    recent = le.list_learning_events(conn, limit=recent_limit, user_id=uid)
    heatmap = activity_heatmap(conn, weeks=26, user_id=uid)

    chats = count_user_messages(conn, user_id=uid)
    first_seen = first_activity_at(conn, user_id=uid)
    days_active = distinct_active_days(conn, user_id=uid)
    try:
        library_skills = len(skills_store.list_skills(conn) or [])
    except Exception:
        library_skills = 0

    preview, user_entry_count, user_chars = _user_profile(uid)

    parts = {
        "chats": chats,
        "saved_events": int(stats_ev.get("events_saved") or 0),
        "user_memory_chars": user_chars,
        "library_skills": library_skills,
        "days_active": days_active,
    }
    bond = compute_bond(**parts)

    diagnostics: dict[str, Any] = {}
    try:
        from app.runtime.agent.learning.state import snapshot as learn_snap

        diagnostics = learn_snap(None)
        if isinstance(diagnostics, dict) and diagnostics:
            first = next(iter(diagnostics.values()), None)
            if isinstance(first, dict):
                diagnostics = first
            else:
                diagnostics = learn_snap("_default")
        else:
            diagnostics = learn_snap("_default")
    except Exception:
        diagnostics = {}

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
        "diagnostics": diagnostics,
        "generated_at": time.time(),
    }


__all__ = [
    "activity_heatmap",
    "companion_snapshot",
    "count_user_messages",
    "distinct_active_days",
    "first_activity_at",
    "session_user_id",
]

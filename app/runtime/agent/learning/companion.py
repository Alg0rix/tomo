"""Companion read model — bond, rhythm, what Tomo knows, and the diary.

Owns SQL aggregates that are *not* ledger CRUD (user messages, active days).
Ledger insert/list stay in ``app.models.mixins.learning_events``.

Multi-user: all aggregates and previews are scoped by ``user_id`` (login account).
Day buckets use the viewer's UTC offset (``tz_minutes``, minutes east of UTC)
so "today" and streaks match the browser's calendar, not UTC.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.models.mixins import learning_events as le
from app.models.mixins import sessions as sessions_store
from app.models.mixins import settings as settings_store
from app.runtime.agent.learning.bond import bond_breakdown, bond_stage, compute_bond
from app.runtime.agent.learning.diary import describe_learned

_MAX_TZ_MINUTES = 14 * 60
_SKIPPED_NOTE = re.compile(
    r"empty choices|LLM request failed|Provider returned no output|no completion"
    r"|^error:|review round limit",
    re.I,
)


def clamp_tz(tz_minutes: int | None) -> int:
    try:
        tz = int(tz_minutes or 0)
    except (TypeError, ValueError):
        return 0
    return max(-_MAX_TZ_MINUTES, min(_MAX_TZ_MINUTES, tz))


def _local_today(tz_minutes: int) -> date:
    return (datetime.now(timezone.utc) + timedelta(minutes=tz_minutes)).date()


def _days_together(first_seen_at: float | None, tz_minutes: int = 0) -> int:
    if not first_seen_at or first_seen_at <= 0:
        return 0
    start = (
        datetime.fromtimestamp(float(first_seen_at), tz=timezone.utc)
        + timedelta(minutes=tz_minutes)
    ).date()
    return max(0, (_local_today(tz_minutes) - start).days)


def session_user_id(conn: sqlite3.Connection, session_id: str | None) -> str:
    sid = (session_id or "").strip()
    if not sid:
        return "web"
    sess = sessions_store.get_session(conn, sid)
    if not sess:
        return "web"
    return (sess.get("user_id") or "web").strip() or "web"


# -- aggregates -------------------------------------------------------------


def count_user_messages(conn: sqlite3.Connection, *, user_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM messages m JOIN sessions s ON s.id = m.session_id "
        "WHERE m.type='user' AND s.user_id=?",
        (user_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def first_activity_at(conn: sqlite3.Connection, *, user_id: str) -> float | None:
    """Earliest real activity for this account."""
    row = conn.execute(
        "SELECT MIN(t) AS t FROM ("
        " SELECT MIN(m.ts) AS t FROM messages m JOIN sessions s ON s.id = m.session_id"
        "  WHERE m.type='user' AND m.ts > 0 AND s.user_id=?"
        " UNION ALL SELECT MIN(created_at) FROM sessions WHERE created_at > 0 AND user_id=?"
        " UNION ALL SELECT MIN(created_at) FROM learning_events"
        "  WHERE created_at > 0 AND user_id=?)",
        (user_id, user_id, user_id),
    ).fetchone()
    return float(row["t"]) if row and row["t"] else None


def day_counts(
    conn: sqlite3.Connection, *, user_id: str, tz_minutes: int = 0
) -> dict[str, dict[str, int]]:
    """Per local day: chats (user msgs), reviews, and saves (lessons kept)."""
    shift = f"{clamp_tz(tz_minutes):+d} minutes"
    days: dict[str, dict[str, int]] = {}

    def bucket(key: str) -> dict[str, int]:
        return days.setdefault(key, {"chats": 0, "reviews": 0, "saves": 0})

    for row in conn.execute(
        "SELECT date(m.ts, 'unixepoch', ?) AS d, COUNT(*) AS c FROM messages m "
        "JOIN sessions s ON s.id = m.session_id "
        "WHERE m.type='user' AND m.ts > 0 AND s.user_id=? GROUP BY d",
        (shift, user_id),
    ):
        bucket(row["d"])["chats"] = int(row["c"])
    for row in conn.execute(
        "SELECT date(created_at, 'unixepoch', ?) AS d, COUNT(*) AS c, SUM(saved) AS s "
        "FROM learning_events WHERE created_at > 0 AND user_id=? GROUP BY d",
        (shift, user_id),
    ):
        b = bucket(row["d"])
        b["reviews"] = int(row["c"])
        b["saves"] = int(row["s"] or 0)
    return days


def _streaks(active: set[str], today: date) -> tuple[int, int]:
    """(current, longest). Current survives until today ends without activity."""
    check = today if today.isoformat() in active else today - timedelta(days=1)
    current = 0
    while check.isoformat() in active:
        current += 1
        check -= timedelta(days=1)
    longest = run = 0
    prev: date | None = None
    for key in sorted(active):
        d = date.fromisoformat(key)
        run = run + 1 if prev and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    return current, longest


def rhythm(
    counts: dict[str, dict[str, int]], *, weeks: int = 20, tz_minutes: int = 0
) -> dict[str, Any]:
    """Day grid for the last ``weeks`` (Mon-start columns) plus streak facts."""
    n_weeks = max(4, min(int(weeks or 20), 52))
    today = _local_today(tz_minutes)
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=n_weeks - 1)
    out: list[dict[str, Any]] = []
    cur = start
    while cur <= today:
        c = counts.get(cur.isoformat()) or {}
        chats, saves = int(c.get("chats") or 0), int(c.get("saves") or 0)
        out.append(
            {
                "date": cur.isoformat(),
                "weekday": cur.weekday(),
                "chats": chats,
                "reviews": int(c.get("reviews") or 0),
                "saves": saves,
                "intensity": chats + 3 * saves,
            }
        )
        cur += timedelta(days=1)

    active = {k for k, c in counts.items() if c.get("chats") or c.get("reviews")}
    current, longest = _streaks(active, today)
    by_weekday = [0] * 7
    for key, c in counts.items():
        by_weekday[date.fromisoformat(key).weekday()] += int(c.get("chats") or 0)
    favourite = max(range(7), key=lambda i: by_weekday[i]) if any(by_weekday) else None
    return {
        "weeks": n_weeks,
        "start": start.isoformat(),
        "today": today.isoformat(),
        "days": out,
        "max_intensity": max((d["intensity"] for d in out), default=0),
        "streak": current,
        "longest_streak": longest,
        "active_days": len(active),
        "favourite_weekday": favourite,
    }


# -- what Tomo knows --------------------------------------------------------


def profile_facts(user_id: str, *, limit: int = 60) -> dict[str, Any]:
    """Live user/profile facts with their entry numbers (for edit/forget)."""
    try:
        from app.runtime.memory.vault import doc, paths

        path = paths.entity_path(user_id, "user/profile")
        if not path.is_file():
            return {"facts": [], "total": 0, "chars": 0}
        page = doc.parse(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"facts": [], "total": 0, "chars": 0}
    live = []
    for number, entry in enumerate(page.entries):
        data = doc.fact_data(entry)
        if data["superseded"] or not data["text"]:
            continue
        live.append({"number": number, "text": data["text"], "origin": data["origin"]})
    chars = len("\n".join(f["text"] for f in live))
    # Newest first — entries are appended in order.
    return {"facts": live[::-1][:limit], "total": len(live), "chars": chars}


def vault_pages(user_id: str) -> dict[str, int]:
    """Entity page counts per type (excluding the profile page itself)."""
    try:
        from app.runtime.memory.vault import paths

        root = paths.vault_root(user_id) / "entities"
    except ValueError:
        return {}
    counts: dict[str, int] = {}
    if not root.is_dir():
        return counts
    for kind in sorted(paths.TYPES):
        folder = root / kind
        if not folder.is_dir():
            continue
        n = sum(1 for p in folder.glob("*.md") if not (kind == "user" and p.stem == "profile"))
        if n:
            counts[kind] = n
    return counts


def _episode_count(conn: sqlite3.Connection, user_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM episodic_memories "
            "WHERE user_id=? AND state != 'archived' AND superseded_by = ''",
            (user_id,),
        ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row["c"] if row else 0)


def skills_together(conn: sqlite3.Connection) -> dict[str, Any]:
    """Skills grown with the user: library (learned/installed) or ever loaded.

    The bundled catalog is not collaboration, so it never counts toward bond.
    """
    rows = conn.execute(
        "SELECT id, name, source, use_count, last_used_at, created_at FROM skills "
        "WHERE enabled=1 AND COALESCE(archived_at, 0)=0 "
        "AND (source='library' OR use_count > 0)"
    ).fetchall()
    skills = [dict(r) for r in rows]
    most_used = sorted(
        (s for s in skills if s["use_count"] > 0),
        key=lambda s: (-s["use_count"], -(s["last_used_at"] or 0), s["id"]),
    )[:5]
    library = sorted(
        (s for s in skills if s["source"] == "library"),
        key=lambda s: str(s["created_at"] or ""),
        reverse=True,
    )
    keep = ("id", "name", "use_count", "last_used_at")
    return {
        "count": len(skills),
        "library": len(library),
        "most_used": [{k: s[k] for k in keep} for s in most_used],
    }


# -- diary ------------------------------------------------------------------

_LEGACY_LABELS = {
    "memory": ("fact", "Saved a note"),
    "manage_skill": ("skill", "Worked on a skill"),
    "record_episode": ("episode", "Remembered an episode"),
    "save_artifact": ("file", "Kept a file"),
    "agent_state": ("state", "Updated working state"),
}


def _learned_item(item: dict[str, Any]) -> dict[str, Any] | None:
    tool = item.get("tool") or ""
    if tool not in _LEGACY_LABELS or not item.get("saved_eligible"):
        return None
    kind, fallback = _LEGACY_LABELS[tool]
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    out: dict[str, Any] = {"kind": kind, "text": (detail and describe_learned(item)) or fallback}
    if tool == "memory":
        entity = detail.get("entity") or ""
        action = detail.get("action") or ""
        out.update(
            {
                "verb": {"replace": "corrected", "remove": "forgot"}.get(action, "noted"),
                "entity": entity,
                "fact": detail.get("content") or "",
                "href": f"/memory#{entity}" if entity else "/memory",
            }
        )
    elif tool == "manage_skill":
        sid = detail.get("skill_id") or ""
        action = detail.get("action") or ""
        out.update(
            {
                "verb": {"create": "wrote", "delete": "retired", "merge": "merged"}.get(
                    action, "refined"
                ),
                "name": detail.get("name") or sid,
                "href": f"/skills/{sid}" if sid and action != "delete" else "",
            }
        )
    elif tool == "record_episode":
        out.update({"verb": "remembered", "title": detail.get("title") or ""})
    return out


def event_view(ev: dict[str, Any], titles: dict[str, str] | None = None) -> dict[str, Any]:
    """Diary-ready view of a ledger row (works for rows from older builds)."""
    extract = ev.get("extract") if isinstance(ev.get("extract"), dict) else {}
    learned = [
        x for x in (_learned_item(i) for i in extract.get("items") or [] if isinstance(i, dict)) if x
    ]
    note = str(ev.get("note") or "")
    if ev.get("saved"):
        status = "learned"
    elif _SKIPPED_NOTE.search(note):
        status = "skipped"
    else:
        status = "quiet"
    diary = str(ev.get("diary") or "").strip()
    # Pre-detail builds wrote "Recorded: memory: Saved vault fact." — not prose.
    if diary.startswith("Recorded:"):
        diary = " ".join(x["text"] for x in learned if x.get("fact") or x.get("title") or x.get("name"))
    if status == "learned" and not learned:
        learned = [{"kind": "note", "text": diary or "Kept a lesson for next time."}]
    sid = str(ev.get("session_id") or "")
    focus = [k for k, on in (("memory", ev.get("review_memory")), ("skills", ev.get("review_skills"))) if on]
    return {
        "id": ev.get("id"),
        "created_at": ev.get("created_at"),
        "agent_id": ev.get("agent_id") or "",
        "status": status,
        "story": diary,
        "learned": learned,
        "focus": focus,
        "session": (
            {"id": sid, "title": (titles or {}).get(sid, ""), "exists": sid in (titles or {})}
            if sid
            else None
        ),
    }


def _session_titles(conn: sqlite3.Connection, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, title FROM sessions WHERE id IN ({marks})", tuple(ids)
    ).fetchall()
    return {r["id"]: (r["title"] or "").strip() for r in rows}


def diary_page(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    limit: int = 30,
    before: float | None = None,
    learned_only: bool = False,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """One page of the diary, newest first, with an exact ``has_more`` flag."""
    lim = max(1, min(int(limit or 30), 100))
    rows = le.list_learning_events(
        conn,
        limit=lim + 1,
        before=before,
        agent_id=agent_id,
        saved_only=learned_only,
        user_id=user_id,
    )
    has_more = len(rows) > lim
    rows = rows[:lim]
    titles = _session_titles(conn, {str(r.get("session_id") or "") for r in rows})
    entries = [event_view(r, titles) for r in rows]
    return {
        "entries": entries,
        "has_more": has_more,
        "next_before": rows[-1]["created_at"] if has_more and rows else None,
    }


# -- learning loop status -----------------------------------------------------


def learning_status(agent_id: str | None, *, enabled: bool) -> dict[str, Any]:
    """What the learning loop is doing *now*, in human terms.

    Call outside the store lock: it may hydrate counters from the DB.
    """
    try:
        from app.runtime.agent.learning.state import snapshot

        snap = snapshot(agent_id or "_default")
    except Exception:
        snap = {}
    nudge = int(snap.get("memory_nudge") or 0)
    since = int(snap.get("turns_since_memory") or 0)
    cooldown = float(snap.get("cooldown_remaining_sec") or 0)
    if not enabled:
        mode = "off"
    elif snap.get("in_flight"):
        mode = "reviewing"
    elif cooldown > 0:
        mode = "resting"
    elif snap.get("memory_due") or snap.get("skills_due"):
        mode = "due"
    else:
        mode = "listening"
    return {
        "mode": mode,
        "turns_until_review": max(0, nudge - since) if nudge > 0 else None,
        "memory_nudge": nudge,
        "turns_since_review": since,
        "cooldown_remaining_sec": round(cooldown),
        "last_review_at": float(snap.get("last_review_at") or 0) or None,
        "reviews_started": int(snap.get("reviews_started") or 0),
        "reviews_saved": int(snap.get("reviews_saved") or 0),
    }


# -- snapshot ---------------------------------------------------------------


def companion_snapshot(
    conn: sqlite3.Connection,
    *,
    recent_limit: int = 30,
    user_id: str | None = None,
    tz_minutes: int = 0,
) -> dict[str, Any]:
    """Full payload for GET /api/companion (single connection, per account).

    ``learning`` status is added by the caller outside the DB lock.
    """
    uid = (user_id or "").strip() or "web"
    tz = clamp_tz(tz_minutes)
    settings = settings_store.get_settings(conn)

    counts = day_counts(conn, user_id=uid, tz_minutes=tz)
    rhythm_view = rhythm(counts, weeks=20, tz_minutes=tz)
    stats_ev = le.learning_event_stats(conn, user_id=uid)
    profile = profile_facts(uid)
    skills = skills_together(conn)
    first_seen = first_activity_at(conn, user_id=uid)

    values = {
        "chats": count_user_messages(conn, user_id=uid),
        "saved_events": int(stats_ev.get("events_saved") or 0),
        "user_memory_chars": profile["chars"],
        "library_skills": skills["count"],
        "days_active": rhythm_view["active_days"],
    }
    bond = compute_bond(**values)
    parts = bond_breakdown(**values)

    return {
        "bond": bond,
        "bond_parts": parts,
        "stage": bond_stage(bond, parts),
        "first_seen_at": first_seen,
        "days_together": _days_together(first_seen, tz),
        "learning_enabled": bool(settings.get("learning_enabled", True)),
        "stats": {
            **stats_ev,
            "chats": values["chats"],
            "profile_facts": profile["total"],
            "episodes": _episode_count(conn, uid),
            "skills_together": skills["count"],
        },
        "rhythm": rhythm_view,
        "profile": profile,
        "vault_pages": vault_pages(uid),
        "skills": skills,
        "diary": diary_page(conn, user_id=uid, limit=recent_limit),
        "generated_at": time.time(),
    }


__all__ = [
    "clamp_tz",
    "companion_snapshot",
    "count_user_messages",
    "day_counts",
    "diary_page",
    "event_view",
    "first_activity_at",
    "learning_status",
    "profile_facts",
    "rhythm",
    "session_user_id",
    "skills_together",
    "vault_pages",
]

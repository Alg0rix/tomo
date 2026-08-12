"""Learning-event ledger — append-only review rows for Companion growth log.

CRUD only. Companion bond aggregates live in
``app.runtime.agent.learning.companion``.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> float:
    return time.time()


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return val if isinstance(val, list) else []


def _parse_json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return val if isinstance(val, dict) else {}


def _row_keys(row: sqlite3.Row) -> set[str]:
    try:
        return set(row.keys())
    except Exception:
        return set()


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    keys = _row_keys(row)
    extract = {}
    if "extract_json" in keys:
        extract = _parse_json_obj(row["extract_json"])
    return {
        "id": row["id"],
        "created_at": float(row["created_at"] or 0),
        "agent_id": row["agent_id"] or "",
        "session_id": row["session_id"] or "",
        "user_id": row["user_id"] or "web",
        "reason": row["reason"] or "",
        "review_memory": bool(row["review_memory"]),
        "review_skills": bool(row["review_skills"]),
        "saved": bool(row["saved"]),
        "actions": _parse_json_list(row["actions_json"]),
        "diary": row["diary"] or "",
        "note": row["note"] or "",
        "plan": _parse_json_obj(row["plan_json"]),
        "extract": extract,
        "memory_types": list(extract.get("memory_types") or [])
        if isinstance(extract, dict)
        else [],
    }


def insert_learning_event(
    conn: sqlite3.Connection,
    *,
    agent_id: str = "",
    session_id: str = "",
    user_id: str = "web",
    reason: str = "",
    review_memory: bool = False,
    review_skills: bool = False,
    saved: bool = False,
    actions: list[str] | None = None,
    diary: str = "",
    note: str = "",
    plan: dict[str, Any] | None = None,
    extract: dict[str, Any] | None = None,
    event_id: str | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    eid = (event_id or "").strip() or uuid.uuid4().hex
    ts = float(created_at) if created_at is not None else _now()
    acts = [str(a) for a in (actions or []) if str(a).strip()]
    extract_obj = extract if isinstance(extract, dict) else {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(learning_events)")}
    if "extract_json" in cols:
        conn.execute(
            """
            INSERT INTO learning_events (
                id, created_at, agent_id, session_id, user_id, reason,
                review_memory, review_skills, saved, actions_json, diary, note,
                plan_json, extract_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                ts,
                (agent_id or "").strip(),
                (session_id or "").strip(),
                (user_id or "web").strip() or "web",
                (reason or "").strip(),
                1 if review_memory else 0,
                1 if review_skills else 0,
                1 if saved else 0,
                json.dumps(acts, ensure_ascii=False),
                (diary or "").strip(),
                (note or "").strip(),
                json.dumps(plan or {}, ensure_ascii=False),
                json.dumps(extract_obj, ensure_ascii=False),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO learning_events (
                id, created_at, agent_id, session_id, user_id, reason,
                review_memory, review_skills, saved, actions_json, diary, note, plan_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                ts,
                (agent_id or "").strip(),
                (session_id or "").strip(),
                (user_id or "web").strip() or "web",
                (reason or "").strip(),
                1 if review_memory else 0,
                1 if review_skills else 0,
                1 if saved else 0,
                json.dumps(acts, ensure_ascii=False),
                (diary or "").strip(),
                (note or "").strip(),
                json.dumps(plan or {}, ensure_ascii=False),
            ),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM learning_events WHERE id=?", (eid,)
    ).fetchone()
    return _row_to_event(row)  # type: ignore[arg-type]


def list_learning_events(
    conn: sqlite3.Connection,
    *,
    limit: int = 30,
    before: float | None = None,
    agent_id: str | None = None,
    saved_only: bool = False,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 30), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if before is not None:
        clauses.append("created_at < ?")
        params.append(float(before))
    aid = (agent_id or "").strip()
    if aid:
        clauses.append("agent_id = ?")
        params.append(aid)
    if saved_only:
        clauses.append("saved = 1")
    uid = (user_id or "").strip()
    if uid:
        clauses.append("user_id = ?")
        params.append(uid)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(lim)
    rows = conn.execute(
        f"""
        SELECT * FROM learning_events
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def learning_event_stats(
    conn: sqlite3.Connection, *, user_id: str | None = None
) -> dict[str, int]:
    uid = (user_id or "").strip()
    if uid:
        total = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM learning_events WHERE user_id=?",
                (uid,),
            ).fetchone()["c"]
        )
        saved = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM learning_events "
                "WHERE saved=1 AND user_id=?",
                (uid,),
            ).fetchone()["c"]
        )
    else:
        total = int(
            conn.execute("SELECT COUNT(*) AS c FROM learning_events").fetchone()["c"]
        )
        saved = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM learning_events WHERE saved=1"
            ).fetchone()["c"]
        )
    return {
        "events_total": total,
        "events_saved": saved,
        "events_idle": total - saved,
    }


def learning_events_by_month(
    conn: sqlite3.Connection, *, months: int = 12, user_id: str | None = None
) -> list[dict[str, Any]]:
    """Last ``months`` calendar months (UTC) with event counts."""
    n = max(1, min(int(months or 12), 36))
    now = datetime.now(timezone.utc)
    buckets: list[dict[str, Any]] = []
    y, m = now.year, now.month
    for _ in range(n):
        buckets.append({"month": f"{y:04d}-{m:02d}", "events": 0, "saved": 0})
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    buckets.reverse()
    by_key = {b["month"]: b for b in buckets}
    y0, m0 = int(buckets[0]["month"][:4]), int(buckets[0]["month"][5:7])
    start_ts = datetime(y0, m0, 1, tzinfo=timezone.utc).timestamp()

    uid = (user_id or "").strip()
    if uid:
        rows = conn.execute(
            "SELECT created_at, saved FROM learning_events "
            "WHERE created_at >= ? AND user_id=?",
            (start_ts, uid),
        )
    else:
        rows = conn.execute(
            "SELECT created_at, saved FROM learning_events WHERE created_at >= ?",
            (start_ts,),
        )
    for row in rows:
        ts = float(row["created_at"] or 0)
        if ts <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        b = by_key.get(f"{dt.year:04d}-{dt.month:02d}")
        if not b:
            continue
        b["events"] += 1
        if row["saved"]:
            b["saved"] += 1
    return buckets


def get_learning_agent_state(
    conn: sqlite3.Connection, agent_id: str
) -> dict[str, Any] | None:
    aid = (agent_id or "").strip() or "_default"
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "learning_agent_state" not in tables:
        return None
    row = conn.execute(
        "SELECT * FROM learning_agent_state WHERE agent_id=?", (aid,)
    ).fetchone()
    if not row:
        return None
    return {
        "agent_id": row["agent_id"],
        "turns_since_memory": int(row["turns_since_memory"] or 0),
        "iters_since_skill": int(row["iters_since_skill"] or 0),
        "memory_due": bool(row["memory_due"]),
        "skills_due": bool(row["skills_due"]),
        "skill_refine_pending": bool(row["skill_refine_pending"]),
        "last_review_at": float(row["last_review_at"] or 0),
        "reviews_started": int(row["reviews_started"] or 0),
        "reviews_saved": int(row["reviews_saved"] or 0),
        "skipped_cooldown": int(row["skipped_cooldown"] or 0),
        "skipped_inflight": int(row["skipped_inflight"] or 0),
        "updated_at": float(row["updated_at"] or 0),
    }


def upsert_learning_agent_state(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    turns_since_memory: int = 0,
    iters_since_skill: int = 0,
    memory_due: bool = False,
    skills_due: bool = False,
    skill_refine_pending: bool = False,
    last_review_at: float = 0.0,
    reviews_started: int = 0,
    reviews_saved: int = 0,
    skipped_cooldown: int = 0,
    skipped_inflight: int = 0,
) -> None:
    aid = (agent_id or "").strip() or "_default"
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "learning_agent_state" not in tables:
        return
    now = _now()
    conn.execute(
        """
        INSERT INTO learning_agent_state (
            agent_id, turns_since_memory, iters_since_skill, memory_due, skills_due,
            skill_refine_pending, last_review_at, reviews_started, reviews_saved,
            skipped_cooldown, skipped_inflight, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            turns_since_memory=excluded.turns_since_memory,
            iters_since_skill=excluded.iters_since_skill,
            memory_due=excluded.memory_due,
            skills_due=excluded.skills_due,
            skill_refine_pending=excluded.skill_refine_pending,
            last_review_at=excluded.last_review_at,
            reviews_started=excluded.reviews_started,
            reviews_saved=excluded.reviews_saved,
            skipped_cooldown=excluded.skipped_cooldown,
            skipped_inflight=excluded.skipped_inflight,
            updated_at=excluded.updated_at
        """,
        (
            aid,
            int(turns_since_memory),
            int(iters_since_skill),
            1 if memory_due else 0,
            1 if skills_due else 0,
            1 if skill_refine_pending else 0,
            float(last_review_at or 0),
            int(reviews_started),
            int(reviews_saved),
            int(skipped_cooldown),
            int(skipped_inflight),
            now,
        ),
    )
    conn.commit()


__all__ = [
    "insert_learning_event",
    "list_learning_events",
    "learning_event_stats",
    "learning_events_by_month",
    "get_learning_agent_state",
    "upsert_learning_agent_state",
]

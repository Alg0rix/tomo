"""Usage event ledger — Token Monitor module (turns + token estimates)."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any


def record_event(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    agent_id: str = "",
    turns: int = 1,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    message_preview: str = "",
    created_at: float | None = None,
) -> dict[str, Any]:
    ts = float(created_at if created_at is not None else time.time())
    preview = (message_preview or "").strip().replace("\n", " ")[:160]
    cur = conn.execute(
        "INSERT INTO usage_events "
        "(session_id, agent_id, created_at, turns, prompt_tokens, completion_tokens, "
        "message_preview) VALUES (?,?,?,?,?,?,?)",
        (
            session_id,
            (agent_id or "").strip(),
            ts,
            max(1, int(turns)),
            max(0, int(prompt_tokens)),
            max(0, int(completion_tokens)),
            preview,
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid or 0),
        "session_id": session_id,
        "agent_id": (agent_id or "").strip(),
        "created_at": ts,
        "turns": max(1, int(turns)),
        "prompt_tokens": max(0, int(prompt_tokens)),
        "completion_tokens": max(0, int(completion_tokens)),
        "message_preview": preview,
    }


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def heatmap(
    conn: sqlite3.Connection, *, days: int = 365
) -> list[dict[str, Any]]:
    """Daily turn/token counts for the last ``days`` (UTC), including empty days."""
    now = time.time()
    cutoff = now - (days * 86400)
    rows = conn.execute(
        "SELECT created_at, turns, prompt_tokens, completion_tokens "
        "FROM usage_events WHERE created_at >= ?",
        (cutoff,),
    ).fetchall()
    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        key = _day_key(float(r["created_at"]))
        slot = by_day.setdefault(
            key, {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )
        slot["turns"] += int(r["turns"] or 0)
        slot["prompt_tokens"] += int(r["prompt_tokens"] or 0)
        slot["completion_tokens"] += int(r["completion_tokens"] or 0)

    end = datetime.fromtimestamp(now, tz=timezone.utc).date()
    start = end - timedelta(days=days - 1)
    out: list[dict[str, Any]] = []
    d = start
    while d <= end:
        key = d.isoformat()
        slot = by_day.get(key, {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0})
        tokens = slot["prompt_tokens"] + slot["completion_tokens"]
        out.append(
            {
                "date": key,
                "turns": slot["turns"],
                "prompt_tokens": slot["prompt_tokens"],
                "completion_tokens": slot["completion_tokens"],
                "tokens": tokens,
            }
        )
        d += timedelta(days=1)
    return out


def leaderboard_agents(
    conn: sqlite3.Connection, *, limit: int = 10, days: int = 365
) -> list[dict[str, Any]]:
    cutoff = time.time() - (days * 86400)
    rows = conn.execute(
        "SELECT agent_id, "
        "SUM(turns) AS turns, "
        "SUM(prompt_tokens) AS prompt_tokens, "
        "SUM(completion_tokens) AS completion_tokens, "
        "COUNT(*) AS events "
        "FROM usage_events "
        "WHERE created_at >= ? AND agent_id != '' "
        "GROUP BY agent_id "
        "ORDER BY turns DESC, prompt_tokens + completion_tokens DESC "
        "LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [
        {
            "agent_id": r["agent_id"],
            "turns": int(r["turns"] or 0),
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
            "tokens": int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0),
            "events": int(r["events"] or 0),
        }
        for r in rows
    ]


def leaderboard_sessions(
    conn: sqlite3.Connection, *, limit: int = 10, days: int = 365
) -> list[dict[str, Any]]:
    cutoff = time.time() - (days * 86400)
    rows = conn.execute(
        "SELECT e.session_id, "
        "COALESCE(s.title, '') AS title, "
        "SUM(e.turns) AS turns, "
        "SUM(e.prompt_tokens) AS prompt_tokens, "
        "SUM(e.completion_tokens) AS completion_tokens, "
        "COUNT(*) AS events, "
        "MAX(e.created_at) AS last_at "
        "FROM usage_events e "
        "LEFT JOIN sessions s ON s.id = e.session_id "
        "WHERE e.created_at >= ? "
        "GROUP BY e.session_id "
        "ORDER BY turns DESC, prompt_tokens + completion_tokens DESC "
        "LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [
        {
            "session_id": r["session_id"],
            "title": (r["title"] or "").strip() or r["session_id"],
            "turns": int(r["turns"] or 0),
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
            "tokens": int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0),
            "events": int(r["events"] or 0),
            "last_at": float(r["last_at"] or 0),
        }
        for r in rows
    ]


def recent_activity(
    conn: sqlite3.Connection, *, limit: int = 40
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.id, e.session_id, e.agent_id, e.created_at, e.turns, "
        "e.prompt_tokens, e.completion_tokens, e.message_preview, "
        "COALESCE(s.title, '') AS title "
        "FROM usage_events e "
        "LEFT JOIN sessions s ON s.id = e.session_id "
        "ORDER BY e.created_at DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "session_id": r["session_id"],
            "title": (r["title"] or "").strip() or r["session_id"],
            "agent_id": r["agent_id"] or "",
            "created_at": float(r["created_at"] or 0),
            "turns": int(r["turns"] or 0),
            "prompt_tokens": int(r["prompt_tokens"] or 0),
            "completion_tokens": int(r["completion_tokens"] or 0),
            "tokens": int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0),
            "message_preview": r["message_preview"] or "",
        }
        for r in rows
    ]


def summary_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    now = time.time()
    day_ago = now - 86400
    week_ago = now - 7 * 86400

    def _agg(since: float) -> dict[str, int]:
        row = conn.execute(
            "SELECT COALESCE(SUM(turns),0) AS turns, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COUNT(DISTINCT session_id) AS sessions "
            "FROM usage_events WHERE created_at >= ?",
            (since,),
        ).fetchone()
        return {
            "turns": int(row["turns"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "tokens": int(row["prompt_tokens"] or 0) + int(row["completion_tokens"] or 0),
            "sessions": int(row["sessions"] or 0),
        }

    active = conn.execute(
        "SELECT COUNT(DISTINCT session_id) AS n FROM usage_events "
        "WHERE created_at >= ?",
        (now - 3600,),
    ).fetchone()
    return {
        "today": _agg(day_ago),
        "week": _agg(week_ago),
        "active_sessions_1h": int(active["n"] or 0),
    }


def dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full Token Monitor payload for ``GET /api/usage``."""
    return {
        "summary": summary_stats(conn),
        "heatmap": heatmap(conn, days=365),
        "agents": leaderboard_agents(conn, limit=10),
        "sessions": leaderboard_sessions(conn, limit=10),
        "activity": recent_activity(conn, limit=40),
    }


__all__ = [
    "record_event",
    "heatmap",
    "leaderboard_agents",
    "leaderboard_sessions",
    "recent_activity",
    "summary_stats",
    "dashboard",
]

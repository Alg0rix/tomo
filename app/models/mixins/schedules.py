"""Schedules + run log — SQLite (Alpha Slice G).

Interval schedules use ``interval_seconds``; ``cron`` is kept for display /
seed compatibility. The in-process runner fires when ``next_run <= now``.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _now() -> float:
    return time.time()


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("_", (text or "").strip().lower()).strip("_")
    return s[:40] or "sch"


def _new_id(conn: sqlite3.Connection, name: str) -> str:
    base = f"sch_{_slugify(name)}"
    candidate = base
    n = 0
    while conn.execute("SELECT 1 FROM schedules WHERE id=?", (candidate,)).fetchone():
        n += 1
        candidate = f"{base}_{n}"
        if n > 50:
            return f"sch_{uuid.uuid4().hex[:12]}"
    return candidate


def interval_from_cron(cron: str) -> int:
    """Best-effort interval (seconds) from a simple cron expression."""
    parts = (cron or "").strip().split()
    if len(parts) >= 1 and parts[0].startswith("*/"):
        try:
            minutes = int(parts[0][2:])
            if minutes > 0:
                return minutes * 60
        except ValueError:
            pass
    if len(parts) >= 5:
        dow = parts[4]
        if dow not in ("*",) and "-" not in dow and "," not in dow:
            return 604800  # weekly-ish
        return 86400  # daily-ish
    return 3600


def _row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "agent_id": row["agent_id"],
        "cron": row["cron"] or "",
        "interval_seconds": int(row["interval_seconds"] or 0),
        "message": row["message"] or "",
        "enabled": bool(row["enabled"]),
        "last_run": row["last_run"],
        "next_run": row["next_run"],
        "created_at": row["created_at"],
    }


def list_schedules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM schedules ORDER BY created_at ASC"
    ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def get_schedule(conn: sqlite3.Connection, schedule_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM schedules WHERE id=?", (schedule_id,)
    ).fetchone()
    return _row_to_schedule(row) if row else None


def list_due(conn: sqlite3.Connection, now: float | None = None) -> list[dict[str, Any]]:
    """Enabled schedules whose ``next_run`` is due (or overdue)."""
    ts = now if now is not None else _now()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE enabled=1 AND next_run IS NOT NULL "
        "AND next_run <= ? ORDER BY next_run ASC",
        (ts,),
    ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def create_schedule(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    agent_id = (data.get("agent_id") or "").strip()
    if not agent_id:
        raise ValueError("agent_id is required")
    if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise ValueError(f"Agent not found: {agent_id}")

    from app.models.ids import unique_id

    sid = unique_id(
        conn,
        "schedules",
        name=name,
        prefix="sch",
        explicit=(data.get("id") or None) or None,
    )

    cron = (data.get("cron") or "").strip()
    interval = data.get("interval_seconds")
    if interval is None or interval == "":
        interval = interval_from_cron(cron) if cron else 3600
    interval = int(interval)
    if interval <= 0 and not cron:
        raise ValueError("interval_seconds must be > 0 (or provide cron)")
    if interval <= 0:
        interval = interval_from_cron(cron) if cron else 3600

    if not cron and interval > 0:
        if interval % 60 == 0 and interval < 3600:
            cron = f"*/{interval // 60} * * * *"
        else:
            cron = f"every {interval}s"

    message = (data.get("message") or "").strip() or f"[schedule] {name}"
    enabled = 1 if data.get("enabled", True) else 0
    now = _now()
    next_run = data.get("next_run")
    if next_run is None:
        next_run = now + interval if enabled else None

    conn.execute(
        "INSERT INTO schedules (id, name, agent_id, cron, interval_seconds, message, "
        "enabled, last_run, next_run, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            name,
            agent_id,
            cron,
            interval,
            message,
            enabled,
            data.get("last_run"),
            next_run,
            now,
        ),
    )
    conn.commit()
    out = get_schedule(conn, sid)
    assert out is not None
    return out


def update_schedule(
    conn: sqlite3.Connection, schedule_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM schedules WHERE id=?", (schedule_id,)
    ).fetchone()
    if not row:
        return None

    name = data["name"].strip() if "name" in data and data["name"] is not None else row["name"]
    agent_id = row["agent_id"]
    if "agent_id" in data and data["agent_id"] is not None:
        agent_id = str(data["agent_id"]).strip()
        if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise ValueError(f"Agent not found: {agent_id}")

    cron = data.get("cron", row["cron"])
    if cron is None:
        cron = row["cron"]
    cron = str(cron).strip()

    interval = row["interval_seconds"]
    if "interval_seconds" in data and data["interval_seconds"] is not None:
        interval = int(data["interval_seconds"])
        if interval <= 0:
            raise ValueError("interval_seconds must be > 0")

    message = row["message"]
    if "message" in data and data["message"] is not None:
        message = str(data["message"])

    enabled = row["enabled"]
    if "enabled" in data and data["enabled"] is not None:
        enabled = 1 if data["enabled"] else 0

    next_run = row["next_run"]
    if "next_run" in data:
        next_run = data["next_run"]
    elif "enabled" in data and data["enabled"] is not None:
        if enabled and next_run is None:
            next_run = _now() + int(interval or 3600)
        elif not enabled:
            next_run = None

    last_run = data["last_run"] if "last_run" in data else row["last_run"]

    conn.execute(
        "UPDATE schedules SET name=?, agent_id=?, cron=?, interval_seconds=?, "
        "message=?, enabled=?, last_run=?, next_run=? WHERE id=?",
        (
            name,
            agent_id,
            cron,
            int(interval or 0),
            message,
            enabled,
            last_run,
            next_run,
            schedule_id,
        ),
    )
    conn.commit()
    return get_schedule(conn, schedule_id)


def delete_schedule(conn: sqlite3.Connection, schedule_id: str) -> bool:
    cur = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    return cur.rowcount > 0


def begin_run(
    conn: sqlite3.Connection,
    schedule_id: str,
    *,
    session_id: str | None = None,
    now: float | None = None,
) -> str:
    """Insert a run row and bump ``last_run`` / ``next_run``. Returns run id."""
    sch = get_schedule(conn, schedule_id)
    if not sch:
        raise ValueError(f"Schedule not found: {schedule_id}")
    ts = now if now is not None else _now()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    interval = int(sch["interval_seconds"] or 3600)
    next_run = ts + max(interval, 1) if sch["enabled"] else None
    conn.execute(
        "INSERT INTO schedule_runs (id, schedule_id, session_id, status, error, "
        "started_at, finished_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, schedule_id, session_id, "running", "", ts, None),
    )
    conn.execute(
        "UPDATE schedules SET last_run=?, next_run=? WHERE id=?",
        (ts, next_run, schedule_id),
    )
    conn.commit()
    return run_id


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str = "ok",
    error: str = "",
    session_id: str | None = None,
    now: float | None = None,
) -> None:
    ts = now if now is not None else _now()
    if session_id is not None:
        conn.execute(
            "UPDATE schedule_runs SET status=?, error=?, finished_at=?, session_id=? "
            "WHERE id=?",
            (status, error or "", ts, session_id, run_id),
        )
    else:
        conn.execute(
            "UPDATE schedule_runs SET status=?, error=?, finished_at=? WHERE id=?",
            (status, error or "", ts, run_id),
        )
    conn.commit()


def list_runs(
    conn: sqlite3.Connection,
    schedule_id: str | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if schedule_id:
        rows = conn.execute(
            "SELECT * FROM schedule_runs WHERE schedule_id=? "
            "ORDER BY started_at DESC LIMIT ?",
            (schedule_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schedule_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "schedule_id": r["schedule_id"],
            "session_id": r["session_id"],
            "status": r["status"],
            "error": r["error"] or "",
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
        }
        for r in rows
    ]


__all__ = [
    "interval_from_cron",
    "list_schedules",
    "get_schedule",
    "list_due",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "begin_run",
    "finish_run",
    "list_runs",
]

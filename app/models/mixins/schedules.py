"""Schedules + run log — SQLite harness (hermes-inspired).

Supports interval, 5-field cron, and one-shot schedules. Firing uses
claim-before-run CAS so concurrent ticks cannot double-fire. ``cron`` and
``interval_seconds`` remain for backward compatibility with the Alpha UI.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any

from app.scheduler.parse import (
    MIN_INTERVAL_SECONDS,
    compute_next_run,
    parse_schedule,
    parsed_from_row,
)

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
CLAIM_TTL_SECONDS = 300


def _now() -> float:
    return time.time()


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("_", (text or "").strip().lower()).strip("_")
    return s[:40] or "sch"


def interval_from_cron(cron: str) -> int:
    """Best-effort interval (seconds) from a simple cron / every expression."""
    text = (cron or "").strip()
    if not text:
        return 3600
    try:
        parsed = parse_schedule(text if text.lower().startswith("every ") else f"every {text}" if text[0].isdigit() else text)
        if parsed["kind"] == "interval":
            return int(parsed["interval_seconds"])
    except ValueError:
        pass
    lower = text.lower()
    if lower.startswith("every "):
        try:
            from app.scheduler.parse import parse_duration

            return parse_duration(text[6:])
        except ValueError:
            return 3600
    parts = text.split()
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
            return 604800
        return 86400
    return 3600


def _row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    kind = row["schedule_kind"] if "schedule_kind" in keys else "interval"
    display = row["schedule_display"] if "schedule_display" in keys else ""
    expr = row["schedule_expr"] if "schedule_expr" in keys else ""
    state = row["state"] if "state" in keys else (
        "scheduled" if row["enabled"] else "paused"
    )
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
        "schedule_kind": (kind or "interval") or "interval",
        "schedule_display": display or (row["cron"] or ""),
        "schedule_expr": expr or "",
        "state": state or "scheduled",
        "pause_reason": (row["pause_reason"] if "pause_reason" in keys else "") or "",
        "repeat_times": row["repeat_times"] if "repeat_times" in keys else None,
        "run_count": int((row["run_count"] if "run_count" in keys else 0) or 0),
        "claim_until": row["claim_until"] if "claim_until" in keys else None,
    }


def list_schedules(
    conn: sqlite3.Connection, *, include_disabled: bool = True
) -> list[dict[str, Any]]:
    if include_disabled:
        rows = conn.execute(
            "SELECT * FROM schedules ORDER BY created_at ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE enabled=1 ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def get_schedule(conn: sqlite3.Connection, schedule_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM schedules WHERE id=?", (schedule_id,)
    ).fetchone()
    return _row_to_schedule(row) if row else None


def resolve_schedule_ref(
    conn: sqlite3.Connection, ref: str
) -> dict[str, Any] | None:
    """Resolve by id, or unique name (case-insensitive). Raises on ambiguous name."""
    text = (ref or "").strip()
    if not text:
        return None
    hit = get_schedule(conn, text)
    if hit:
        return hit
    rows = conn.execute(
        "SELECT * FROM schedules WHERE lower(name)=lower(?)", (text,)
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise ValueError(
            f"Ambiguous schedule name '{text}': "
            + ", ".join(r["id"] for r in rows)
        )
    return _row_to_schedule(rows[0])


def list_due(conn: sqlite3.Connection, now: float | None = None) -> list[dict[str, Any]]:
    """Enabled, non-completed schedules whose ``next_run`` is due and unclaimed."""
    ts = now if now is not None else _now()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE enabled=1 "
        "AND COALESCE(state, 'scheduled') NOT IN ('paused', 'completed') "
        "AND next_run IS NOT NULL AND next_run <= ? "
        "AND (claim_until IS NULL OR claim_until < ?) "
        "ORDER BY next_run ASC",
        (ts, ts),
    ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def _apply_parsed(
    data: dict[str, Any], parsed: dict[str, Any], *, now: float
) -> dict[str, Any]:
    """Merge parse result into create/update field bag."""
    kind = parsed["kind"]
    data["schedule_kind"] = kind
    data["schedule_display"] = parsed.get("display") or ""
    data["schedule_expr"] = parsed.get("expr") or ""
    if kind == "interval":
        data["interval_seconds"] = int(parsed["interval_seconds"])
        data["cron"] = data.get("cron") or parsed["display"]
    elif kind == "cron":
        data["interval_seconds"] = 0
        data["cron"] = parsed["expr"]
    else:  # once
        data["interval_seconds"] = 0
        data["cron"] = parsed.get("display") or "once"
        if data.get("repeat_times") is None:
            data["repeat_times"] = 1
    return data


def create_schedule(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name:
        # Derive from message when agent tool omits name
        msg = (data.get("message") or "").strip()
        name = (msg[:50].strip() if msg else "") or "schedule"
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

    now = _now()
    schedule_str = (data.get("schedule") or "").strip()
    cron = (data.get("cron") or "").strip()
    interval = data.get("interval_seconds")

    parsed: dict[str, Any] | None = None
    if schedule_str:
        parsed = parse_schedule(schedule_str, now=now)
    elif cron and not interval:
        try:
            parsed = parse_schedule(cron, now=now)
        except ValueError:
            # legacy free-text cron kept as display with interval heuristic
            interval = interval_from_cron(cron)
    elif interval is not None and interval != "":
        parsed = parse_schedule(f"every {int(interval)}s", now=now)
    elif cron:
        try:
            parsed = parse_schedule(cron, now=now)
        except ValueError:
            parsed = parse_schedule(f"every {interval_from_cron(cron)}s", now=now)
    else:
        parsed = parse_schedule("every 1h", now=now)

    fields: dict[str, Any] = dict(data)
    _apply_parsed(fields, parsed, now=now)

    if fields.get("schedule_kind") == "interval":
        interval_v = int(fields.get("interval_seconds") or 0)
        if interval_v < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}")

    message = (fields.get("message") or "").strip() or f"[schedule] {name}"
    enabled = 1 if fields.get("enabled", True) else 0
    state = "scheduled" if enabled else "paused"
    if fields.get("state"):
        state = str(fields["state"]).strip().lower()
        valid_states = {"scheduled", "paused", "completed"}
        if state not in valid_states:
            raise ValueError(f"state must be one of {sorted(valid_states)}")
        if state == "completed":
            enabled = 0
        elif state == "paused":
            enabled = 0
        elif state == "scheduled":
            enabled = 1

    repeat_times = fields.get("repeat_times")
    if repeat_times is not None:
        repeat_times = int(repeat_times)
        if repeat_times <= 0:
            repeat_times = None

    next_run = fields.get("next_run")
    if state == "completed":
        next_run = None
        enabled = 0
    if next_run is None and enabled:
        next_run = compute_next_run(parsed, now=now, last_run=None)
        if parsed["kind"] == "once" and next_run is None:
            raise ValueError(
                "One-shot time is more than grace window in the past "
                "and cannot be scheduled."
            )

    conn.execute(
        "INSERT INTO schedules (id, name, agent_id, cron, interval_seconds, message, "
        "enabled, last_run, next_run, created_at, schedule_kind, schedule_display, "
        "schedule_expr, state, pause_reason, repeat_times, run_count, claim_until) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            name,
            agent_id,
            fields.get("cron") or "",
            int(fields.get("interval_seconds") or 0),
            message,
            enabled,
            fields.get("last_run"),
            next_run,
            now,
            fields.get("schedule_kind") or "interval",
            fields.get("schedule_display") or "",
            fields.get("schedule_expr") or "",
            state,
            (fields.get("pause_reason") or ""),
            repeat_times,
            int(fields.get("run_count") or 0),
            None,
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

    sch = _row_to_schedule(row)
    name = data["name"].strip() if "name" in data and data["name"] is not None else sch["name"]
    agent_id = sch["agent_id"]
    if "agent_id" in data and data["agent_id"] is not None:
        agent_id = str(data["agent_id"]).strip()
        if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise ValueError(f"Agent not found: {agent_id}")

    message = sch["message"]
    if "message" in data and data["message"] is not None:
        message = str(data["message"])

    cron = sch["cron"]
    interval = sch["interval_seconds"]
    kind = sch["schedule_kind"]
    display = sch["schedule_display"]
    expr = sch["schedule_expr"]
    next_run = sch["next_run"]
    now = _now()

    schedule_str = data.get("schedule")
    if schedule_str is not None and str(schedule_str).strip():
        parsed = parse_schedule(str(schedule_str).strip(), now=now)
        bag: dict[str, Any] = {}
        _apply_parsed(bag, parsed, now=now)
        kind = bag["schedule_kind"]
        display = bag["schedule_display"]
        expr = bag["schedule_expr"]
        cron = bag.get("cron") or cron
        interval = int(bag.get("interval_seconds") or 0)
        if sch.get("state") != "paused":
            next_run = compute_next_run(
                parsed, now=now, last_run=sch.get("last_run")
            )
    else:
        if "cron" in data and data["cron"] is not None:
            cron = str(data["cron"]).strip()
        if "interval_seconds" in data and data["interval_seconds"] is not None:
            interval = int(data["interval_seconds"])
            if interval < MIN_INTERVAL_SECONDS and kind == "interval":
                raise ValueError(f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}")
            kind = "interval"
            display = f"every {interval}s"
            try:
                parsed = parse_schedule(f"every {interval}s", now=now)
                next_run = compute_next_run(
                    parsed, now=now, last_run=sch.get("last_run")
                )
            except ValueError:
                pass

    enabled = sch["enabled"]
    state = sch["state"]
    pause_reason = sch.get("pause_reason") or ""
    if "enabled" in data and data["enabled"] is not None:
        enabled = bool(data["enabled"])
        if enabled:
            if sch.get("state") == "completed":
                raise ValueError(
                    "Cannot enable a completed one-shot schedule; create a new one."
                )
            state = "scheduled"
            pause_reason = ""
            if next_run is None:
                parsed = parsed_from_row(
                    {
                        **sch,
                        "schedule_kind": kind,
                        "schedule_expr": expr,
                        "interval_seconds": interval,
                        "cron": cron,
                    }
                )
                next_run = compute_next_run(
                    parsed, now=now, last_run=sch.get("last_run")
                )
        else:
            state = "paused"
            next_run = None
    if "state" in data and data["state"] is not None:
        state = str(data["state"]).strip().lower()
        valid_states = {"scheduled", "paused", "completed"}
        if state not in valid_states:
            raise ValueError(f"state must be one of {sorted(valid_states)}")
        if state == "completed":
            enabled = False
            next_run = None
        elif state == "paused":
            enabled = False
            next_run = None
        elif state == "scheduled":
            if sch.get("state") == "completed" and "enabled" not in data:
                raise ValueError(
                    "Cannot re-schedule a completed one-shot; create a new one."
                )
            enabled = True
    if "pause_reason" in data and data["pause_reason"] is not None:
        pause_reason = str(data["pause_reason"])

    if "next_run" in data:
        next_run = data["next_run"]

    last_run = data["last_run"] if "last_run" in data else sch["last_run"]
    repeat_times = sch.get("repeat_times")
    if "repeat_times" in data:
        rt = data["repeat_times"]
        repeat_times = None if rt is None or int(rt) <= 0 else int(rt)
    run_count = sch.get("run_count") or 0
    if "run_count" in data and data["run_count"] is not None:
        run_count = int(data["run_count"])
    claim_until = sch.get("claim_until")
    if "claim_until" in data:
        claim_until = data["claim_until"]

    conn.execute(
        "UPDATE schedules SET name=?, agent_id=?, cron=?, interval_seconds=?, "
        "message=?, enabled=?, last_run=?, next_run=?, schedule_kind=?, "
        "schedule_display=?, schedule_expr=?, state=?, pause_reason=?, "
        "repeat_times=?, run_count=?, claim_until=? WHERE id=?",
        (
            name,
            agent_id,
            cron,
            int(interval or 0),
            message,
            1 if enabled else 0,
            last_run,
            next_run,
            kind,
            display,
            expr,
            state,
            pause_reason,
            repeat_times,
            run_count,
            claim_until,
            schedule_id,
        ),
    )
    conn.commit()
    return get_schedule(conn, schedule_id)


def pause_schedule(
    conn: sqlite3.Connection, schedule_id: str, *, reason: str = ""
) -> dict[str, Any] | None:
    return update_schedule(
        conn,
        schedule_id,
        {
            "enabled": False,
            "state": "paused",
            "pause_reason": reason or "",
            "next_run": None,
            "claim_until": None,
        },
    )


def resume_schedule(
    conn: sqlite3.Connection, schedule_id: str
) -> dict[str, Any] | None:
    sch = get_schedule(conn, schedule_id)
    if not sch:
        return None
    if sch.get("state") == "completed":
        raise ValueError("Cannot resume a completed one-shot schedule; create a new one.")
    parsed = parsed_from_row(sch)
    now = _now()
    next_run = compute_next_run(parsed, now=now, last_run=sch.get("last_run"))
    return update_schedule(
        conn,
        schedule_id,
        {
            "enabled": True,
            "state": "scheduled",
            "pause_reason": "",
            "next_run": next_run,
            "claim_until": None,
        },
    )


def delete_schedule(conn: sqlite3.Connection, schedule_id: str) -> bool:
    cur = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()
    return cur.rowcount > 0


def claim_for_fire(
    conn: sqlite3.Connection,
    schedule_id: str,
    *,
    now: float | None = None,
    claim_ttl: float = CLAIM_TTL_SECONDS,
) -> dict[str, Any] | None:
    """At-most-once claim. Returns schedule row if claimed, else None."""
    ts = now if now is not None else _now()
    claim_until = ts + claim_ttl
    # Advance next_run optimistically so a concurrent tick won't re-select.
    sch = get_schedule(conn, schedule_id)
    if not sch:
        return None
    if not sch["enabled"] or sch.get("state") in ("paused", "completed"):
        return None
    if sch.get("next_run") is None or float(sch["next_run"]) > ts:
        return None
    cu = sch.get("claim_until")
    if cu is not None and float(cu) >= ts:
        return None

    parsed = parsed_from_row(sch)
    # Tentative next after this fire (finalized in begin_run / finish)
    tentative_next = compute_next_run(parsed, now=ts, last_run=ts, from_time=ts)

    cur = conn.execute(
        "UPDATE schedules SET claim_until=?, next_run=? "
        "WHERE id=? AND enabled=1 "
        "AND next_run IS NOT NULL AND next_run <= ? "
        "AND (claim_until IS NULL OR claim_until < ?)",
        (claim_until, tentative_next, schedule_id, ts, ts),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return get_schedule(conn, schedule_id)


def begin_run(
    conn: sqlite3.Connection,
    schedule_id: str,
    *,
    session_id: str | None = None,
    now: float | None = None,
    claimed: bool = False,
) -> str:
    """Insert a run row and bump ``last_run`` / ``next_run``. Returns run id.

    When ``claimed`` is True the caller already advanced next_run via
    :func:`claim_for_fire`; we only stamp last_run and clear claim on finish.
    """
    sch = get_schedule(conn, schedule_id)
    if not sch:
        raise ValueError(f"Schedule not found: {schedule_id}")
    ts = now if now is not None else _now()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    parsed = parsed_from_row(sch)
    next_run = sch.get("next_run")
    if not claimed:
        next_run = compute_next_run(parsed, now=ts, last_run=ts, from_time=ts)
        if not sch["enabled"] or sch.get("state") in ("paused", "completed"):
            next_run = None

    conn.execute(
        "INSERT INTO schedule_runs (id, schedule_id, session_id, status, error, "
        "started_at, finished_at) VALUES (?,?,?,?,?,?,?)",
        (run_id, schedule_id, session_id, "running", "", ts, None),
    )
    # run_count is incremented only on successful finish (avoids burning
    # repeat_times / one-shot budgets when the process dies mid-run).
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
    row = conn.execute(
        "SELECT schedule_id FROM schedule_runs WHERE id=?", (run_id,)
    ).fetchone()
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

    if row:
        sid = row["schedule_id"]
        sch = get_schedule(conn, sid)
        if sch:
            updates: dict[str, Any] = {"claim_until": None}
            # Count only finished runs toward repeat / one-shot budgets.
            if status == "ok":
                run_count = int(sch.get("run_count") or 0) + 1
                updates["run_count"] = run_count
                kind = sch.get("schedule_kind") or "interval"
                repeat = sch.get("repeat_times")
                done = False
                if kind == "once":
                    done = True
                elif repeat is not None and run_count >= int(repeat):
                    done = True
                if done:
                    updates.update(
                        {
                            "enabled": False,
                            "state": "completed",
                            "next_run": None,
                        }
                    )
            update_schedule(conn, sid, updates)
            return
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
    "CLAIM_TTL_SECONDS",
    "interval_from_cron",
    "list_schedules",
    "get_schedule",
    "resolve_schedule_ref",
    "list_due",
    "create_schedule",
    "update_schedule",
    "pause_schedule",
    "resume_schedule",
    "delete_schedule",
    "claim_for_fire",
    "begin_run",
    "finish_run",
    "list_runs",
]

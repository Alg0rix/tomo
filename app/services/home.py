"""Home page snapshot: what needs the user, what is running, today, rooms.

Everything here is scoped to one login account. Live parts (``needs`` and
``live``) are cheap and polled; the full snapshot adds today's timeline,
foundations health, household, recent chats, and room cards.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.services import store

logger = logging.getLogger(__name__)

_RUNNING_JOBS = {"starting", "running", "stopping"}


def _text(value: Any, limit: int = 120) -> str:
    out = " ".join(str(value or "").split())
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _local_href(value: Any) -> str:
    href = str(value or "").strip()
    parts = urlsplit(href)
    if not href.startswith("/") or href.startswith("//") or parts.scheme or parts.netloc:
        return ""
    return href[:300]


# -- needs ---------------------------------------------------------------------


def _approval_title(payload: dict[str, Any]) -> str:
    args = payload.get("args_preview")
    if isinstance(args, dict):
        for key in ("command", "cmd", "path", "url", "code"):
            if args.get(key):
                return _text(args[key], 160)
    return _text(payload.get("description") or payload.get("tool"), 160)


def needs(user_id: str, sessions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Unresolved approvals, questions, and secure-input requests across chats."""
    from app.runtime.permissions import hitl
    from app.services import secret_store

    out: list[dict[str, Any]] = []
    now_mono = time.monotonic()
    now = time.time()

    def chat(sid: str) -> dict[str, Any]:
        s = sessions.get(sid) or {}
        return {"id": sid, "title": s.get("title") or "Conversation", "channel": s.get("channel") or "web"}

    for pending in list(hitl._approvals.values()):
        sid = pending.session_id or ""
        if pending.event.is_set() or sid not in sessions:
            continue
        p = pending.payload
        out.append(
            {
                "kind": "approval",
                "id": p.get("id"),
                "tool": p.get("tool") or "",
                "title": _approval_title(p),
                "description": _text(p.get("description"), 200),
                "risk": bool(p.get("findings")) or bool(p.get("smart_denied")),
                "choices": [c for c in p.get("choices") or [] if c in {"once", "deny"}],
                "chat": chat(sid),
                "at": now - (now_mono - pending.created_at),
            }
        )
    for pending in list(hitl._clarifies.values()):
        sid = pending.session_id or ""
        if pending.event.is_set() or sid not in sessions:
            continue
        p = pending.payload
        out.append(
            {
                "kind": "question",
                "id": p.get("id"),
                "title": _text(p.get("question"), 240),
                "choices": [_text(c, 60) for c in p.get("choices") or []][:4],
                "chat": chat(sid),
                "at": now - (now_mono - pending.created_at),
            }
        )
    for sid in sessions:
        for req in secret_store.pending_for_session(sid):
            out.append(
                {
                    "kind": "secret",
                    "id": req.get("id"),
                    "title": f"Secure input requested: {_text(req.get('name'), 64)}",
                    "chat": chat(sid),
                    "at": float(req.get("expires_at") or now) - 100,
                }
            )
    out.sort(key=lambda n: n.get("at") or 0, reverse=True)
    return out


# -- live ----------------------------------------------------------------------


def _swarm_runs(session_ids: list[str]) -> list[dict[str, Any]]:
    if not session_ids:
        return []
    marks = ",".join("?" * len(session_ids))
    with store._lock:
        runs = store._conn.execute(
            f"SELECT id, session_id, request, created_at FROM swarm_runs "
            f"WHERE status='running' AND session_id IN ({marks}) "
            f"ORDER BY created_at DESC LIMIT 5",
            tuple(session_ids),
        ).fetchall()
        out = []
        for run in runs:
            tasks = store._conn.execute(
                "SELECT agent_id, status FROM swarm_tasks WHERE run_id=? ORDER BY created_at",
                (run["id"],),
            ).fetchall()
            out.append(
                {
                    "id": run["id"],
                    "session_id": run["session_id"],
                    "request": run["request"],
                    "started_at": run["created_at"],
                    "tasks": [{"agent_id": t["agent_id"], "status": t["status"]} for t in tasks],
                }
            )
    return out


def live(
    user_id: str,
    sessions: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Swarm runs, running chat turns, background processes, next routines."""
    items: list[dict[str, Any]] = []
    sids = list(sessions)

    def name(aid: str) -> str:
        return (agents.get(aid) or {}).get("name") or aid or "Tomo"

    swarm_sessions: set[str] = set()
    for run in _swarm_runs(sids):
        swarm_sessions.add(run["session_id"])
        tasks = run["tasks"]
        done = sum(t["status"] in {"done", "completed", "succeeded"} for t in tasks)
        crew = []
        for t in tasks:
            if t["agent_id"] not in crew:
                crew.append(t["agent_id"])
        items.append(
            {
                "kind": "swarm",
                "title": _text(sessions[run["session_id"]].get("title") or run["request"], 80),
                "detail": f"{len(crew)} agents · {done} of {len(tasks)} tasks done"
                if tasks
                else "Planning tasks",
                "crew": [name(a) for a in crew][:6],
                "lanes": [t["status"] for t in tasks][:12],
                "since": run["started_at"],
                "href": f"/sessions?s={run['session_id']}",
            }
        )

    with store._lock:
        busy = store._busy
        turns = {
            sid: sorted(busy.ids_for_session(sid))
            for sid in sids
            if busy.is_session_turn_active(sid) and sid not in swarm_sessions
        }
    for sid, ids in turns.items():
        s = sessions[sid]
        who = ", ".join(name(a) for a in ids) or name(s.get("agent_id") or "")
        items.append(
            {
                "kind": "turn",
                "title": _text(s.get("title") or "Conversation", 80),
                "detail": f"{who} · via {s.get('channel') or 'web'}",
                "since": s.get("updated_at"),
                "href": f"/sessions?s={sid}",
            }
        )

    if sids:
        marks = ",".join("?" * len(sids))
        with store._lock:
            rows = store._conn.execute(
                f"SELECT payload_json FROM background_jobs WHERE session_id IN ({marks}) "
                f"AND status IN ('starting','running','stopping') ORDER BY rowid DESC LIMIT 6",
                tuple(sids),
            ).fetchall()
        for row in rows:
            try:
                job = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if job.get("user_id") not in (None, user_id) or job.get("status") not in _RUNNING_JOBS:
                continue
            where = job.get("workplace_id") or job.get("cwd") or ""
            items.append(
                {
                    "kind": "process",
                    "title": _text(job.get("command"), 80),
                    "detail": " · ".join(
                        x for x in (_text(where, 60), f"started by {name(job.get('agent_id') or '')}") if x
                    ),
                    "status": job.get("status"),
                    "since": job.get("started_at"),
                    "href": f"/sessions?s={job.get('session_id')}",
                }
            )

    upcoming = [
        s
        for s in store.list_schedules(include_disabled=False)
        if s.get("next_run") and s.get("state") not in {"completed", "paused"}
    ]
    upcoming.sort(key=lambda s: s["next_run"])
    for s in upcoming[:2]:
        items.append(
            {
                "kind": "routine",
                "title": _text(s.get("name") or "Routine", 80),
                "detail": " · ".join(
                    x for x in (s.get("schedule_display") or s.get("cron") or "", name(s.get("agent_id") or "")) if x
                ),
                "at": s["next_run"],
                "href": "/scheduler",
            }
        )
    return items


# -- today ---------------------------------------------------------------------


def _local_day(tz_minutes: int) -> tuple[date, float, float]:
    tz = timezone(timedelta(minutes=tz_minutes))
    today = datetime.now(tz).date()
    start = datetime(today.year, today.month, today.day, tzinfo=tz).timestamp()
    return today, start, start + 86400


def today(
    user_id: str,
    tz_minutes: int,
    sessions: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merged timeline for the viewer's local day (newest last)."""
    from app.runtime.agent.learning import companion
    from app.runtime.memory.vault import journal, paths

    day, start, end = _local_day(tz_minutes)
    items: list[dict[str, Any]] = []

    # Journal turn notes are written with server-local HH:MM headings.
    try:
        path = paths.timeline_path(user_id, day.isoformat())
        body = path.read_text(encoding="utf-8") if path.is_file() else ""
    except (ValueError, OSError):
        body = ""
    for entry in journal.entries(body):
        at = None
        if entry["time"]:
            hh, mm = entry["time"].split(":")
            at = datetime(day.year, day.month, day.day, int(hh), int(mm)).timestamp()
        sid = entry["session"]
        items.append(
            {
                "kind": "chat",
                "at": at,
                "title": _text(entry["outcome"] or entry["goal"] or " ".join(entry["notes"]), 140),
                "meta": (sessions.get(sid) or {}).get("title") or "",
                "href": f"/sessions?s={sid}" if sid in sessions else "/memory",
            }
        )

    with store._lock:
        rows = store._conn.execute(
            "SELECT * FROM learning_events WHERE user_id=? AND saved=1 "
            "AND created_at>=? AND created_at<? ORDER BY created_at",
            (user_id, start, end),
        ).fetchall()
        runs = store._conn.execute(
            "SELECT r.started_at, r.status, r.delivery_target, s.name FROM schedule_runs r "
            "JOIN schedules s ON s.id=r.schedule_id WHERE r.started_at>=? AND r.started_at<? "
            "AND (r.session_id IS NULL OR r.session_id IN (SELECT id FROM sessions WHERE user_id=?)) "
            "ORDER BY r.started_at",
            (start, end, user_id),
        ).fetchall()
    from app.models.mixins import learning_events as le

    for row in rows:
        view = companion.event_view(le._row_to_event(row))
        for learned in view["learned"][:2]:
            items.append(
                {
                    "kind": "learned",
                    "at": view["created_at"],
                    "title": _text(learned.get("text"), 140),
                    "meta": learned.get("kind") or "",
                    "href": _local_href(learned.get("href")) or "/companion",
                }
            )
    for run in runs:
        items.append(
            {
                "kind": "routine",
                "at": run["started_at"],
                "title": _text(run["name"], 80),
                "meta": "failed" if run["status"] not in {"ok", "success", "succeeded"} else (
                    "sent to Telegram" if (run["delivery_target"] or "").startswith("telegram") else "ran"
                ),
                "href": "/scheduler",
                "status": "error" if run["status"] not in {"ok", "success", "succeeded"} else "ok",
            }
        )
    items.sort(key=lambda i: i.get("at") or start)

    upcoming = []
    for s in store.list_schedules(include_disabled=False):
        nxt = s.get("next_run")
        if nxt and time.time() <= nxt < end and s.get("state") not in {"completed", "paused"}:
            upcoming.append({"kind": "routine", "at": nxt, "title": _text(s.get("name"), 80), "href": "/scheduler"})
    upcoming.sort(key=lambda i: i["at"])
    return {"date": day.isoformat(), "items": items[-14:], "upcoming": upcoming[:4]}


# -- foundations / household / recent -----------------------------------------


def foundations(user_id: str) -> list[dict[str, Any]]:
    from app.core.self_update import package_version
    from app.runtime.agent.learning import companion

    out: list[dict[str, Any]] = []
    profile = store.resolve_llm_profile()
    out.append(
        {
            "key": "brain",
            "label": "Brain",
            "value": (profile or {}).get("model") or (profile or {}).get("name") or "not set",
            "state": "ok" if profile else "warn",
            "href": "/system#models",
        }
    )
    with store._lock:
        linked = store._conn.execute(
            "SELECT 1 FROM telegram_account_links WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone()
    token = bool(store.get_settings().get("telegram_bot_token"))
    out.append(
        {
            "key": "telegram",
            "label": "Telegram",
            "value": "linked" if linked else ("not linked" if token else "off"),
            "state": "ok" if linked else ("warn" if token else "off"),
            "href": "/system#shared_channel",
        }
    )
    wps = [w for w in store.list_workplaces() if w.get("enabled", True)]
    if wps:
        online = sum(bool(w.get("online")) for w in wps)
        out.append(
            {
                "key": "workplaces",
                "label": "Workplaces",
                "value": f"{online}/{len(wps)} online",
                "state": "ok" if online == len(wps) else "warn",
                "href": "/workplaces",
            }
        )
    servers = [s for s in store.list_mcp_servers() if s.get("enabled")]
    if servers:
        bad = sum(str(s.get("status") or "") == "error" for s in servers)
        out.append(
            {
                "key": "mcp",
                "label": "MCP",
                "value": f"{len(servers)}" + (f" · {bad} failing" if bad else ""),
                "state": "warn" if bad else "ok",
                "href": "/system#mcp",
            }
        )
    pages = sum(companion.vault_pages(user_id).values())
    out.append(
        {"key": "memory", "label": "Memory", "value": f"{pages} pages", "state": "ok", "href": "/memory"}
    )
    out.append(
        {"key": "version", "label": "Tomo", "value": f"v{package_version()}", "state": "ok", "href": "/system#general"}
    )
    return out


def household(
    agents: dict[str, dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    pending: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    waiting = {n["chat"]["id"] for n in pending if n["kind"] in {"approval", "question"}}
    with store._lock:
        busy_in = {
            sid: store._busy.ids_for_session(sid)
            for sid in sessions
            if store._busy.is_session_turn_active(sid)
        }
    out = []
    for aid, agent in agents.items():
        state, activity = ("idle", agent.get("role") or "ready") if agent.get("enabled") else ("off", "disabled")
        for sid, ids in busy_in.items():
            if aid in ids:
                title = sessions[sid].get("title") or "a chat"
                if sid in waiting:
                    state, activity = "busy", f"waiting on you · {title}"
                else:
                    state, activity = "busy", f"working on {title}"
                break
        out.append(
            {
                "id": aid,
                "name": agent.get("name") or aid,
                "state": state,
                "activity": _text(activity, 60),
                "is_super": bool(agent.get("is_super")),
            }
        )
    rank = {"busy": 0, "idle": 1, "off": 2}
    out.sort(key=lambda a: (rank[a["state"]], not a["is_super"]))
    return out


def recent(sessions: dict[str, dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    rows = sorted(sessions.values(), key=lambda s: s.get("updated_at") or 0, reverse=True)
    return [
        {
            "id": s["id"],
            "title": s.get("title") or "Conversation",
            "via": "swarm" if s.get("is_swarm") else s.get("channel") or "web",
            "updated_at": s.get("updated_at"),
            "active": store.is_session_turn_active(s["id"]),
        }
        for s in rows[:limit]
    ]


# -- rooms ---------------------------------------------------------------------

_TONES = {"", "hot", "warm", "ok", "info"}


def _tone(value: Any) -> str:
    return value if value in _TONES else ""


def _unit(value: Any) -> float | None:
    """A 0–1 fraction, or None when not a number."""
    if isinstance(value, bool):
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and abs(value) != float("inf") else None


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _who(value: Any) -> dict[str, str] | None:
    """Person/agent chip: ``{"name": "Kai", "agent": "kai"}``."""
    if not isinstance(value, dict) or not value.get("name"):
        return None
    out = {"name": _text(value["name"], 24)}
    agent = str(value.get("agent") or "")
    if agent and len(agent) <= 64 and all(c.isalnum() or c in "_-" for c in agent):
        out["agent"] = agent
    return out


def _trend(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value.get("text"):
        return None
    return {
        "text": _text(value["text"], 40),
        "direction": value.get("direction") if value.get("direction") in {"up", "down"} else "",
        "good": bool(value.get("good")),
    }


def _with(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Add optional keys only when they carry a value."""
    row.update({k: v for k, v in extra.items() if v})
    return row


def normalize_card(data: Any) -> dict[str, Any]:
    """Validate a plugin card payload into the fixed render contract.

    Every field is optional; unknown keys are dropped, text is truncated,
    numbers are clamped, and links must be local paths.
    """
    if not isinstance(data, dict):
        raise ValueError("Card must be an object")
    out: dict[str, Any] = {}
    status = data.get("status")
    if isinstance(status, dict) and status.get("text"):
        out["status"] = {"text": _text(status["text"], 20), "tone": _tone(status.get("tone"))}
    notice = data.get("notice")
    if isinstance(notice, dict) and notice.get("text"):
        out["notice"] = {"text": _text(notice["text"], 140), "tone": _tone(notice.get("tone"))}
    metric = data.get("metric")
    if isinstance(metric, dict) and metric.get("value") not in (None, ""):
        out["metric"] = _with(
            {"value": _text(metric["value"], 24), "label": _text(metric.get("label"), 48)},
            tone=_tone(metric.get("tone")),
        )
    if data.get("caption"):
        out["caption"] = _text(data["caption"], 140)
    if trend := _trend(data.get("trend")):
        out["trend"] = trend
    stats = [
        _with(
            {"label": _text(s["label"], 24), "value": _text(s["value"], 16)},
            trend=_trend(s.get("trend")),
            tone=_tone(s.get("tone")),
        )
        for s in _items(data.get("stats"))
        if isinstance(s, dict) and s.get("label") and s.get("value") not in (None, "")
    ]
    if stats:
        out["stats"] = stats[:4]
    image = data.get("image")
    if isinstance(image, dict):
        src = _local_href(image.get("src"))
        if src:
            out["image"] = _with({"src": src}, alt=_text(image.get("alt"), 80))
    bars = []
    for bar in _items(data.get("bars")):
        if not isinstance(bar, dict) or not bar.get("label") or (value := _unit(bar.get("value") or 0)) is None:
            continue
        bars.append({"label": _text(bar["label"], 40), "value": value, "text": _text(bar.get("text"), 20), "tone": _tone(bar.get("tone"))})
    if bars:
        out["bars"] = bars[:5]
    chart = []
    for point in _items(data.get("chart")):
        if isinstance(point, dict) and (value := _number(point.get("value"))) is not None:
            chart.append(_with({"label": _text(point.get("label"), 12), "value": max(0.0, value)}, tone=_tone(point.get("tone"))))
    if len(chart) >= 2:
        out["chart"] = chart[-31:]
    ring = data.get("ring")
    if isinstance(ring, dict):
        segments = []
        for seg in _items(ring.get("segments")):
            if isinstance(seg, dict) and seg.get("label") and (value := _number(seg.get("value"))) is not None and value > 0:
                segments.append(_with({"label": _text(seg["label"], 24), "value": value}, text=_text(seg.get("text"), 16), tone=_tone(seg.get("tone"))))
        if segments:
            out["ring"] = _with({"segments": segments[:6]}, value=_text(ring.get("value"), 12), label=_text(ring.get("label"), 20))
    gauge = data.get("gauge")
    if isinstance(gauge, dict) and (value := _unit(gauge.get("value") or 0)) is not None:
        out["gauge"] = _with(
            {"value": value},
            label=_text(gauge.get("label"), 40),
            text=_text(gauge.get("text"), 24),
            tone=_tone(gauge.get("tone")),
        )
    heatmap = data.get("heatmap")
    if isinstance(heatmap, dict):
        cells = [max(0.0, v) for v in (_number(v) for v in _items(heatmap.get("values"))) if v is not None]
        if cells:
            out["heatmap"] = _with({"values": cells[-140:]}, label=_text(heatmap.get("label"), 40))
    if spark := [v for v in (_number(v) for v in _items(data.get("spark"))) if v is not None][-30:]:
        if len(spark) >= 2:
            out["spark"] = spark
    series = data.get("series")
    if isinstance(series, dict):
        lines = []
        for ln in _items(series.get("lines")):
            if not isinstance(ln, dict):
                continue
            vals = [v for v in (_number(x) for x in _items(ln.get("values"))) if v is not None][-30:]
            if len(vals) >= 2:
                lines.append(_with({"values": vals}, label=_text(ln.get("label"), 24), tone=_tone(ln.get("tone"))))
        if lines:
            out["series"] = _with(
                {"lines": lines[:4]},
                labels=[_text(x, 12) for x in _items(series.get("labels"))][:6],
            )
    rows = []
    for row in _items(data.get("list")):
        if isinstance(row, dict) and row.get("label"):
            rows.append(_with(
                {"label": _text(row["label"], 60), "value": _text(row.get("value"), 24), "href": _local_href(row.get("href"))},
                sub=_text(row.get("sub"), 60), tone=_tone(row.get("tone")), who=_who(row.get("who")),
            ))
    if rows:
        out["list"] = rows[:6]
    table = data.get("table")
    if isinstance(table, dict):
        headers = [_text(c, 16) for c in _items(table.get("columns"))][:4]
        if any(headers):
            body = []
            for r in _items(table.get("rows")):
                if isinstance(r, list):
                    row = [_text(c, 24) for c in r[: len(headers)]]
                    body.append(row + [""] * (len(headers) - len(row)))
            out["table"] = {"columns": headers, "rows": body[:5]}
    timeline = []
    for event in _items(data.get("timeline")):
        if isinstance(event, dict) and event.get("label"):
            timeline.append(_with(
                {"time": _text(event.get("time"), 12), "label": _text(event["label"], 60)},
                meta=_text(event.get("meta"), 40), tone=_tone(event.get("tone")), href=_local_href(event.get("href")),
            ))
    if timeline:
        out["timeline"] = timeline[:5]
    checklist = [
        {"label": _text(c["label"], 60), "done": bool(c.get("done"))}
        for c in _items(data.get("checklist"))
        if isinstance(c, dict) and c.get("label")
    ]
    if checklist:
        out["checklist"] = checklist[:6]
    cols = []
    for col in _items(data.get("columns")):
        if not isinstance(col, dict) or not col.get("title"):
            continue
        cards = [
            _with({"title": _text(c.get("title"), 60), "meta": _text(c.get("meta"), 40)}, who=_who(c.get("who")), tone=_tone(c.get("tone")))
            for c in _items(col.get("items"))
            if isinstance(c, dict) and c.get("title")
        ][:3]
        count = col.get("count")
        cols.append(_with(
            {"title": _text(col["title"], 20), "count": count if isinstance(count, int) and not isinstance(count, bool) else len(cards), "items": cards},
            muted=bool(col.get("muted")),
        ))
    if cols:
        out["columns"] = cols[:4]
    steps = [
        _with({"label": _text(s["label"], 24)}, state=s["state"] if s.get("state") in {"done", "active"} else "")
        for s in _items(data.get("steps"))
        if isinstance(s, dict) and s.get("label")
    ]
    if steps:
        out["steps"] = steps[:5]
    states = []
    for st in _items(data.get("states")):
        if isinstance(st, dict):
            w = _number(st.get("value"))
            states.append(_with(
                {"tone": _tone(st.get("tone")), "value": w if w and w > 0 else 1},
                text=_text(st.get("text"), 40),
            ))
    if states:
        out["states"] = states[:20]
    tags = [
        {"label": _text(t["label"], 24), "tone": _tone(t.get("tone"))}
        for t in _items(data.get("tags"))
        if isinstance(t, dict) and t.get("label")
    ]
    if tags:
        out["tags"] = tags[:8]
    if data.get("quote"):
        out["quote"] = _text(data["quote"], 240)
    code = "\n".join(str(data.get("code") or "").splitlines()[:6])[:400].rstrip()
    if code:
        out["code"] = code
    meter = data.get("meter")
    if isinstance(meter, dict) and (value := _unit(meter.get("value") or 0)) is not None:
        out["meter"] = {"value": value, "label": _text(meter.get("label"), 40), "text": _text(meter.get("text"), 30)}
    if data.get("foot"):
        out["foot"] = _text(data["foot"], 80)
    actions = []
    for action in _items(data.get("actions")):
        if not isinstance(action, dict) or not action.get("label"):
            continue
        if action.get("prompt"):
            actions.append({"label": _text(action["label"], 40), "prompt": str(action["prompt"])[:500]})
        elif _local_href(action.get("href")):
            actions.append({"label": _text(action["label"], 40), "href": _local_href(action["href"])})
    if actions:
        out["actions"] = actions[:3]
    if data.get("empty"):
        out["empty"] = _text(data["empty"], 140)
    return out


def _core_cards(user_id: str, tz_minutes: int) -> list[dict[str, Any]]:
    from app.runtime.agent.learning import companion

    cards: list[dict[str, Any]] = []
    try:
        snap = store.companion_snapshot(user_id=user_id, tz_minutes=tz_minutes, recent_limit=8)
    except Exception:
        logger.exception("companion snapshot failed for home")
        snap = None
    pages = companion.vault_pages(user_id)
    week_ago = time.time() - 7 * 86400
    learned: list[dict[str, Any]] = []
    for entry in (snap or {}).get("diary", {}).get("entries", []):
        if (entry.get("created_at") or 0) >= week_ago:
            learned.extend(x for x in entry.get("learned") or [] if x.get("kind") == "memory" or x.get("entity"))
    memory = {
        "metric": {"value": f"+{len(learned)}", "label": "facts this week"},
        "caption": f"{sum(pages.values())} pages across {len(pages)} types",
        "list": [
            {"label": x.get("entity") or x.get("text"), "value": x.get("verb") or "", "href": x.get("href")}
            for x in learned[:3]
        ],
        "actions": [{"label": "What do you know about me?", "prompt": "What do you know about me? Summarize the most useful things you remember."}],
    }
    if not learned and not pages:
        memory = {"empty": "Nothing remembered yet. Tomo writes pages as you chat."}
    cards.append({"key": "core:memory", "plugin": "", "core": "memory", "title": "Memory", "size": "s", "href": "/memory", "data": memory})

    if snap:
        stage = snap.get("stage") or {}
        latest = next((e.get("story") for e in snap.get("diary", {}).get("entries", []) if e.get("story")), "")
        companion_card = {
            "quote": latest or "No diary entry yet — keep chatting and Tomo will start writing.",
            "meter": {
                "value": min(1.0, (snap.get("bond") or 0) / 100),
                "label": f"Bond · {stage.get('name') or ''}".strip(" ·"),
                "text": f"{(snap.get('rhythm') or {}).get('streak', 0)}-day streak",
            },
        }
        cards.append({"key": "core:companion", "plugin": "", "core": "companion", "title": "Companion", "size": "s", "href": "/companion", "data": companion_card})
    return cards


def rooms(
    user_id: str, tz_minutes: int, *, keys: set[str] | None = None
) -> dict[str, Any]:
    from app.plugins.manager import get_manager

    manager = get_manager()
    meta = {p["id"]: p for p in manager.list() if p.get("running")}
    contrib = manager.home_contributions(user_id, keys=keys)
    cards: list[dict[str, Any]] = []
    for card in contrib["cards"]:
        info = meta.get(card["plugin"]) or {}
        pages = info.get("pages") or []
        item = {
            "key": card["key"],
            "plugin": card["plugin"],
            "default_visible": card.get("default_visible", True),
            "plugin_name": info.get("name") or card["plugin"],
            "title": card["title"] or info.get("name") or card["plugin"],
            "icon": info.get("icon") or "puzzle",
            "kanji": card.get("kanji") or "",
            "size": card["size"],
            "href": pages[0]["path"] if pages else "/extensions",
        }
        if "refresh_seconds" in card:
            item["refresh_seconds"] = card["refresh_seconds"]
        if "error" in card:
            item["error"] = card["error"]
        else:
            try:
                item["data"] = normalize_card(card.get("data"))
            except ValueError as exc:
                item["error"] = str(exc)
        cards.append(item)
    if keys is not None:
        return {"cards": cards}
    with_cards = {c["plugin"] for c in cards}
    # Running plugins with pages but no card still get a simple doorway.
    for pid, info in meta.items():
        if pid not in with_cards and info.get("pages"):
            cards.append(
                {
                    "key": f"{pid}:door",
                    "plugin": pid,
                    "title": info.get("name") or pid,
                    "icon": info.get("icon") or "puzzle",
                    "size": "s",
                    "href": info["pages"][0]["path"],
                    "data": {
                        "caption": _text(info.get("description"), 140) or "Open this room.",
                        "list": [{"label": p["label"], "href": p["path"]} for p in info["pages"][1:5]],
                    },
                }
            )
    cards.extend(_core_cards(user_id, tz_minutes))
    starters = [
        {"label": s["label"], "prompt": s["prompt"], "source": (meta.get(s["plugin"]) or {}).get("name") or s["plugin"]}
        for s in contrib["starters"]
    ]
    return {"cards": cards, "starters": starters}


# -- layout --------------------------------------------------------------------


def _layout_path(user_id: str) -> Path:
    from app.core.config import TOMO_HOME

    digest = hashlib.sha256(user_id.encode()).hexdigest()
    return TOMO_HOME / "state" / "home" / f"{digest}.json"


def get_layout(user_id: str) -> dict[str, Any]:
    try:
        data = json.loads(_layout_path(user_id).read_text())
    except (OSError, ValueError):
        return {"order": [], "hidden": []}
    if not isinstance(data, dict):
        return {"order": [], "hidden": []}
    layout = {
        "order": [str(k) for k in data.get("order") or []][:100],
        "hidden": [str(k) for k in data.get("hidden") or []][:100],
    }
    for key in ("selected", "sizes"):
        if key in data:
            layout[key] = data[key]
    return layout


def save_layout(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    def keys(value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("order and hidden must be lists")
        return [str(k)[:120] for k in value if isinstance(k, str)][:100]

    layout = {"order": keys(data.get("order", [])), "hidden": keys(data.get("hidden", []))}
    if "selected" in data:
        layout["selected"] = keys(data["selected"])
    if "sizes" in data:
        sizes = data["sizes"]
        if not isinstance(sizes, dict) or len(sizes) > 100:
            raise ValueError("sizes must be an object with at most 100 widgets")
        layout["sizes"] = {}
        for key, size in sizes.items():
            if not isinstance(size, dict) or size.get("width") not in ("s", "m", "l"):
                raise ValueError("Widget width must be s, m, or l")
            height = size.get("height")
            if height is not None and (type(height) is not int or not 180 <= height <= 900 or height % 20):
                raise ValueError("Widget height must be 180–900 pixels in steps of 20")
            layout["sizes"][str(key)[:120]] = {"width": size["width"], "height": height}
    path = _layout_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(layout))
    tmp.replace(path)
    return layout


# -- entry points --------------------------------------------------------------


def _context(user_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    sessions = {s["id"]: s for s in store.list_sessions(user_id=user_id)}
    agents = {a["id"]: a for a in store.list_agents()}
    return sessions, agents


def live_snapshot(user_id: str) -> dict[str, Any]:
    sessions, agents = _context(user_id)
    pending = needs(user_id, sessions)
    return {
        "needs": pending,
        "live": live(user_id, sessions, agents),
        "household": household(agents, sessions, pending),
        "recent": recent(sessions),
    }


def badges(user_id: str) -> dict[str, int]:
    """Rail counters: open needs and chat turns or swarms in flight."""
    sessions, agents = _context(user_id)
    running = live(user_id, sessions, agents)
    return {
        "needs": len(needs(user_id, sessions)),
        "running": sum(item["kind"] in {"turn", "swarm"} for item in running),
    }


def snapshot(user_id: str, tz_minutes: int = 0) -> dict[str, Any]:
    sessions, agents = _context(user_id)
    pending = needs(user_id, sessions)
    coord = store.get_coordinator()
    room_data = rooms(user_id, tz_minutes)
    return {
        "coordinator": {"id": coord["id"], "name": coord["name"]} if coord else None,
        "needs": pending,
        "live": live(user_id, sessions, agents),
        "today": today(user_id, tz_minutes, sessions, agents),
        "foundations": foundations(user_id),
        "household": household(agents, sessions, pending),
        "recent": recent(sessions),
        "rooms": room_data["cards"],
        "starters": room_data["starters"],
        "layout": get_layout(user_id),
        "generated_at": time.time(),
    }


__all__ = [
    "badges",
    "get_layout",
    "live_snapshot",
    "normalize_card",
    "save_layout",
    "snapshot",
]

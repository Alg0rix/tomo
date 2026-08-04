"""APScheduler wake engine — SQLite stays source of truth.

Design
------
* **DB owns** schedule rows (agent_id, message, state, next_run, runs).
* **APScheduler owns** wake timing only: Cron / Interval / Date triggers call
  back into :func:`app.scheduler.runner.fire_schedule` by schedule id.
* CRUD paths call :func:`sync_schedule` / :func:`remove_schedule` so the
  in-memory job table tracks enabled jobs.
* On boot, :func:`reload_from_db` re-registers every enabled schedule.
* Claim-before-fire and run logs stay in the Tomo store (not APS jobstore).

Not used for telegram, chat turns, learning reviews, or portal I/O — those
remain event/request driven.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_scheduler: Any = None  # AsyncIOScheduler | None
_started = False
_loop = None  # asyncio event loop used by AsyncIOScheduler

MISFIRE_GRACE_SECONDS = 120
BUILTIN_PREFIX = "builtin:"


def _tz():
    try:
        from app.services import store

        name = (store.get_settings().get("timezone") or "").strip()
        if name:
            return ZoneInfo(name)
    except Exception:
        pass
    return datetime.now().astimezone().tzinfo


def _get_scheduler():
    with _lock:
        return _scheduler


def is_running() -> bool:
    with _lock:
        return bool(_started and _scheduler is not None and _scheduler.running)


def start_engine() -> None:
    """Start AsyncIOScheduler and load enabled jobs from SQLite (idempotent)."""
    global _scheduler, _started, _loop
    with _lock:
        if _started and _scheduler is not None and _scheduler.running:
            return
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError as exc:
            logger.error("apscheduler not installed: %s — schedules will not auto-fire", exc)
            return

        import asyncio

        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = None

        tz = _tz()
        _scheduler = AsyncIOScheduler(timezone=tz, event_loop=_loop)
        _scheduler.start(paused=False)
        _started = True
        logger.info("APScheduler started timezone=%s", tz)

    reload_from_db()
    _register_builtins()


async def stop_engine() -> None:
    """Shut down APScheduler (idempotent)."""
    global _scheduler, _started, _loop
    with _lock:
        sch = _scheduler
        _scheduler = None
        _started = False
        _loop = None
    if sch is not None:
        try:
            sch.shutdown(wait=False)
        except Exception:
            logger.debug("APScheduler shutdown error", exc_info=True)
        logger.info("APScheduler stopped")


def reload_from_db() -> int:
    """Register every enabled, non-completed schedule. Returns job count."""
    from app.services.store import store

    n = 0
    try:
        rows = store.list_schedules(include_disabled=False)
    except Exception:
        logger.exception("reload_from_db: list_schedules failed")
        return 0
    for row in rows:
        if row.get("state") in ("paused", "completed"):
            continue
        if not row.get("enabled"):
            continue
        try:
            if sync_schedule(row["id"], row=row):
                n += 1
        except Exception:
            logger.exception("reload_from_db: failed id=%s", row.get("id"))
    logger.info("APScheduler loaded %d schedules from DB", n)
    return n


def sync_schedule(schedule_id: str, *, row: dict[str, Any] | None = None) -> bool:
    """Create or replace the APS job for ``schedule_id``. Returns True if registered."""
    from app.services.store import store

    sch = row or store.get_schedule(schedule_id)
    if not sch:
        remove_schedule(schedule_id)
        return False
    if not sch.get("enabled") or sch.get("state") in ("paused", "completed"):
        remove_schedule(schedule_id)
        return False

    trigger = _build_trigger(sch)
    if trigger is None:
        remove_schedule(schedule_id)
        return False

    aps = _get_scheduler()
    if aps is None or not aps.running:
        return False

    try:
        aps.add_job(
            _on_job_fire,
            trigger=trigger,
            id=schedule_id,
            args=[schedule_id],
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            coalesce=True,
            max_instances=1,
        )
    except Exception:
        logger.exception("APScheduler add_job failed id=%s", schedule_id)
        return False

    _overlay_next_run(schedule_id)
    return True


def remove_schedule(schedule_id: str) -> None:
    aps = _get_scheduler()
    if aps is None:
        return
    try:
        aps.remove_job(schedule_id)
    except Exception:
        pass  # job may not exist


def _build_trigger(sch: dict[str, Any]):
    """Map a Tomo schedule row → APScheduler trigger."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from app.scheduler.parse import parsed_from_row

    parsed = parsed_from_row(sch)
    kind = parsed.get("kind") or "interval"
    tz = _tz()

    if kind == "interval":
        seconds = int(parsed.get("interval_seconds") or sch.get("interval_seconds") or 0)
        if seconds < 1:
            return None
        # Align first fire with stored next_run when still in the future.
        kwargs: dict[str, Any] = {"seconds": seconds, "timezone": tz}
        nxt = sch.get("next_run")
        now = time.time()
        if isinstance(nxt, (int, float)) and float(nxt) > now + 1:
            kwargs["start_date"] = datetime.fromtimestamp(float(nxt), tz=tz)
        return IntervalTrigger(**kwargs)

    if kind == "cron":
        expr = (parsed.get("expr") or sch.get("schedule_expr") or sch.get("cron") or "").strip()
        if not expr:
            return None
        try:
            return CronTrigger.from_crontab(expr, timezone=tz)
        except Exception:
            # Fallback: 5 space-separated fields
            parts = expr.split()
            if len(parts) != 5:
                logger.warning("invalid cron expr for %s: %r", sch.get("id"), expr)
                return None
            return CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                timezone=tz,
            )

    if kind == "once":
        run_at = parsed.get("run_at")
        if run_at is None:
            run_at = sch.get("next_run")
        if run_at is None:
            return None
        try:
            ts = float(run_at)
        except (TypeError, ValueError):
            return None
        if ts < time.time() - MISFIRE_GRACE_SECONDS:
            logger.info(
                "one-shot %s is past grace window — not registering", sch.get("id")
            )
            return None
        return DateTrigger(run_date=datetime.fromtimestamp(ts, tz=tz), timezone=tz)

    return None


async def _on_job_fire(schedule_id: str) -> None:
    """APS callback: claim + run agent turn for this schedule id."""
    from app.scheduler.runner import fire_schedule
    from app.services.store import store

    sch = store.get_schedule(schedule_id)
    if not sch or not sch.get("enabled") or sch.get("state") in ("paused", "completed"):
        remove_schedule(schedule_id)
        return

    # Ensure due-window claim sees this job as due even if clocks skew slightly.
    now = time.time()
    nxt = sch.get("next_run")
    if nxt is not None:
        try:
            if float(nxt) > now + 2:
                # APS woke early — nudge next_run so claim can succeed.
                store.update_schedule(schedule_id, {"next_run": now})
                sch = store.get_schedule(schedule_id) or sch
        except (TypeError, ValueError):
            pass

    try:
        result = await fire_schedule(sch, now=now)
        logger.info(
            "APScheduler fired id=%s status=%s claimed=%s",
            schedule_id,
            result.get("status"),
            result.get("claimed"),
        )
    except Exception:
        logger.exception("APScheduler fire failed id=%s", schedule_id)
    finally:
        # Refresh job / next_run after finish_run may have completed or advanced.
        fresh = store.get_schedule(schedule_id)
        if not fresh or not fresh.get("enabled") or fresh.get("state") in (
            "paused",
            "completed",
        ):
            remove_schedule(schedule_id)
        else:
            # Interval/cron: APS already has next fire; overlay DB next_run.
            # One-shot completed path removes above.
            kind = fresh.get("schedule_kind") or "interval"
            if kind == "once":
                remove_schedule(schedule_id)
            else:
                _overlay_next_run(schedule_id)
                # Re-sync trigger start if next_run drifted far (claim advanced it).
                sync_schedule(schedule_id, row=fresh)


def _overlay_next_run(schedule_id: str) -> None:
    """Write APS next_run_time back onto the SQLite row (display / claim window)."""
    aps = _get_scheduler()
    if aps is None:
        return
    try:
        job = aps.get_job(schedule_id)
    except Exception:
        return
    if not job or not job.next_run_time:
        return
    try:
        from app.services.store import store

        ts = job.next_run_time.timestamp()
        store.update_schedule(schedule_id, {"next_run": ts})
    except Exception:
        logger.debug("overlay next_run failed id=%s", schedule_id, exc_info=True)


def _register_builtins() -> None:
    """Optional housekeeping jobs registered alongside user schedules."""
    aps = _get_scheduler()
    if aps is None:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger

        aps.add_job(
            _builtin_claim_sweep,
            CronTrigger(minute="*/15", timezone=_tz()),
            id=f"{BUILTIN_PREFIX}claim_sweep",
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception:
        logger.debug("builtin claim_sweep register failed", exc_info=True)


async def _builtin_claim_sweep() -> None:
    """Clear stale claims and fire any overdue jobs APS may have missed."""
    from app.scheduler.runner import fire_due_schedules
    from app.services.store import store

    now = time.time()
    try:
        for sch in store.list_schedules(include_disabled=False):
            cu = sch.get("claim_until")
            if cu is not None and float(cu) < now:
                store.update_schedule(sch["id"], {"claim_until": None})
    except Exception:
        logger.debug("claim sweep clear failed", exc_info=True)
    try:
        await fire_due_schedules(now=now)
    except Exception:
        logger.exception("claim sweep fire_due failed")


__all__ = [
    "MISFIRE_GRACE_SECONDS",
    "is_running",
    "start_engine",
    "stop_engine",
    "reload_from_db",
    "sync_schedule",
    "remove_schedule",
]

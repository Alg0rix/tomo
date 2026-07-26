"""In-process interval scheduler — fires due schedules as agent turns.

Alpha Slice G: a lightweight asyncio loop (started from app lifespan) polls
SQLite for enabled schedules with ``next_run <= now``, then drains
:func:`app.services.chat.run_session_turn` for the target agent. Interval
schedules are enough for Alpha; cron strings are display/seed helpers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_runner_task: asyncio.Task[None] | None = None
_runner_stop: asyncio.Event | None = None

# Poll period for the background loop (seconds). Tests call fire_due_* directly.
DEFAULT_POLL_SECONDS = 5.0


async def fire_schedule(
    schedule: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Fire one schedule: begin run → session turn → finish run."""
    from app.services.chat import run_session_turn
    from app.services.store import store

    ts = now if now is not None else time.time()
    schedule_id = schedule["id"]
    agent_id = schedule["agent_id"]
    message = (schedule.get("message") or "").strip() or f"[schedule] {schedule.get('name', schedule_id)}"

    session_id = store.get_or_create_session(agent_id, "scheduler")
    run_id = store.begin_schedule_run(schedule_id, session_id=session_id, now=ts)
    result: dict[str, Any] = {
        "run_id": run_id,
        "schedule_id": schedule_id,
        "session_id": session_id,
        "status": "ok",
        "error": "",
    }
    try:
        async with contextlib.aclosing(
            run_session_turn(session_id, message, "scheduler")
        ) as agen:
            async for _chunk in agen:
                pass
        store.finish_schedule_run(
            run_id, status="ok", session_id=session_id, now=time.time()
        )
    except Exception as exc:  # noqa: BLE001 — record and continue
        logger.exception("schedule fire failed id=%s", schedule_id)
        result["status"] = "error"
        result["error"] = str(exc)
        store.finish_schedule_run(
            run_id,
            status="error",
            error=str(exc),
            session_id=session_id,
            now=time.time(),
        )
    return result


async def fire_due_schedules(*, now: float | None = None) -> list[dict[str, Any]]:
    """Fire all due schedules once. Safe to call from tests."""
    from app.services.store import store

    ts = now if now is not None else time.time()
    due = store.list_due_schedules(ts)
    results: list[dict[str, Any]] = []
    for sch in due:
        results.append(await fire_schedule(sch, now=ts))
    return results


async def _runner_loop(stop: asyncio.Event, poll_seconds: float) -> None:
    while not stop.is_set():
        try:
            await fire_due_schedules()
        except Exception:
            logger.exception("scheduler tick error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass


def start_scheduler(*, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    """Start the background poll loop (idempotent). Called from app lifespan."""
    global _runner_task, _runner_stop
    if _runner_task is not None and not _runner_task.done():
        return
    _runner_stop = asyncio.Event()
    _runner_task = asyncio.create_task(
        _runner_loop(_runner_stop, poll_seconds), name="tomo-scheduler"
    )
    logger.info("scheduler started poll_seconds=%s", poll_seconds)


async def stop_scheduler() -> None:
    """Stop the background poll loop (idempotent)."""
    global _runner_task, _runner_stop
    if _runner_stop is not None:
        _runner_stop.set()
    task = _runner_task
    _runner_task = None
    _runner_stop = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("scheduler stopped")


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "fire_schedule",
    "fire_due_schedules",
    "start_scheduler",
    "stop_scheduler",
]

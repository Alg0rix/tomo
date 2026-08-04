"""In-process schedule harness — fires due jobs as agent turns.

Hermes-inspired hardening:
* claim-before-fire CAS (no double-fire across overlapping ticks)
* real next_run via parse.compute_next_run (interval / cron / one-shot)
* one-shot and repeat_times auto-complete after the last successful claim
* concurrent due jobs run in parallel (bounded gather)
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

DEFAULT_POLL_SECONDS = 5.0
MAX_PARALLEL_FIRES = 4


async def fire_schedule(
    schedule: dict[str, Any],
    *,
    now: float | None = None,
    skip_claim: bool = False,
) -> dict[str, Any]:
    """Fire one schedule: claim → begin run → session turn → finish run.

    When ``skip_claim`` is True (manual run-now), still records a run without
    the due-window claim gate.
    """
    from app.services.chat import run_session_turn
    from app.services.store import store

    ts = now if now is not None else time.time()
    schedule_id = schedule["id"]
    agent_id = schedule["agent_id"]
    message = (schedule.get("message") or "").strip() or (
        f"[schedule] {schedule.get('name', schedule_id)}"
    )

    claimed = not skip_claim
    if claimed:
        claimed_row = store.claim_schedule_for_fire(schedule_id, now=ts)
        if not claimed_row:
            return {
                "run_id": "",
                "schedule_id": schedule_id,
                "session_id": "",
                "status": "skipped",
                "error": "not claimed (already running, paused, or not due)",
                "claimed": False,
            }
        schedule = claimed_row

    session_id = store.get_or_create_session(agent_id, "scheduler")
    run_id = store.begin_schedule_run(
        schedule_id, session_id=session_id, now=ts, claimed=claimed
    )

    result: dict[str, Any] = {
        "run_id": run_id,
        "schedule_id": schedule_id,
        "session_id": session_id,
        "status": "ok",
        "error": "",
        "claimed": claimed,
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
    """Claim and fire all due schedules. Safe to call from tests."""
    from app.services.store import store

    ts = now if now is not None else time.time()
    due = store.list_due_schedules(ts)
    if not due:
        return []

    sem = asyncio.Semaphore(MAX_PARALLEL_FIRES)

    async def _one(sch: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            try:
                return await fire_schedule(sch, now=ts)
            except Exception as exc:
                logger.exception("schedule fire failed id=%s", sch.get("id"))
                return {
                    "run_id": "",
                    "schedule_id": sch.get("id", ""),
                    "session_id": "",
                    "status": "error",
                    "error": str(exc),
                    "claimed": False,
                }

    return list(await asyncio.gather(*[_one(s) for s in due]))


async def run_schedule_now(schedule_id: str) -> dict[str, Any]:
    """Manual trigger — runs immediately outside the due window."""
    from app.services.store import store

    sch = store.get_schedule(schedule_id)
    if not sch:
        raise ValueError(f"Schedule not found: {schedule_id}")
    if sch.get("state") == "completed":
        raise ValueError("Schedule is completed; create a new one to run again.")
    return await fire_schedule(sch, skip_claim=True)


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
    "MAX_PARALLEL_FIRES",
    "fire_schedule",
    "fire_due_schedules",
    "run_schedule_now",
    "start_scheduler",
    "stop_scheduler",
]

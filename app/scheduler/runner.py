"""Schedule fire path + lifespan hooks.

Wake timing is owned by APScheduler (:mod:`app.scheduler.engine`). This module
keeps claim → agent turn → finish_run, plus a test helper that fires due rows
without APS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 5.0  # legacy; safety sweep interval lives in engine
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

    agent_id = schedule["agent_id"]
    message = (schedule.get("message") or "").strip() or (
        f"[schedule] {schedule.get('name', schedule_id)}"
    )
    target = schedule.get("delivery_target")
    # Never share histories/artifacts between jobs aimed at different conversations.
    user_id = f"scheduler:{schedule_id}" if target else "scheduler"
    session_id = store.get_or_create_session(agent_id, user_id)
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
    execution_finished = False
    try:
        from app.channels.delivery import open_delivery

        async with contextlib.AsyncExitStack() as stack:
            delivery = (
                await stack.enter_async_context(open_delivery(target, session_id))
                if target
                else None
            )
            final = ""
            completed = False
            turn_error = ""
            agen = await stack.enter_async_context(
                contextlib.aclosing(
                    run_session_turn(session_id, message, user_id, origin="scheduler")
                )
            )
            async for chunk in agen:
                # The chat service emits complete SSE frames, including coordinator done.
                for frame in chunk.split("\n\n"):
                    event = ""
                    data: dict[str, Any] = {}
                    for line in frame.splitlines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                    if (
                        data.get("delegate_call_id")
                        or data.get("agent_id", agent_id) != agent_id
                    ):
                        continue
                    if event == "done":
                        final = str(data.get("content") or "")
                        completed = True
                    elif event == "error":
                        turn_error = str(data.get("message") or "Scheduled turn failed")
            if not completed:
                raise RuntimeError(
                    turn_error or "Scheduled turn ended without a final result"
                )
            store.finish_schedule_run(
                run_id,
                status="ok",
                session_id=session_id,
                now=time.time(),
                delivery_content=final.strip() if target else None,
            )
            execution_finished = True
            if delivery and final.strip():
                result.update(
                    await _deliver_run(
                        {
                            "run_id": run_id,
                            "session_id": session_id,
                            "target": target,
                            "content": final.strip(),
                        },
                        bound=delivery,
                    )
                )
    except Exception as exc:  # noqa: BLE001 — record and continue
        logger.exception("schedule fire failed id=%s", schedule_id)
        if execution_finished:
            # Transport/cleanup failures must not undo a completed job or its outbox.
            result.setdefault("delivery_status", "unknown")
            result["delivery_error"] = str(exc)
        else:
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


async def _deliver_run(row: dict[str, Any], *, bound=None) -> dict[str, Any]:
    from app.channels.delivery import DeliveryBlocked, open_delivery
    from app.services.store import store

    run_id = row["run_id"]
    if not store.claim_schedule_delivery(run_id):
        return {"delivery_status": "skipped"}
    receipt = None
    error = ""
    try:
        if bound is not None:
            receipt = await bound.send_final(row["content"], delivery_id=run_id)
        else:
            async with open_delivery(row["target"], row["session_id"]) as delivery:
                receipt = await delivery.send_final(row["content"], delivery_id=run_id)
        if not receipt:
            raise RuntimeError("Channel did not confirm delivery")
        status = "sent"
    except DeliveryBlocked as exc:
        status, error = "blocked", str(exc)
    except Exception as exc:
        # A failed/partial network send may already have reached the recipient.
        # No automatic retry without a channel-supported idempotency key.
        status, error = "unknown", str(exc)
        logger.warning("schedule delivery unconfirmed run=%s", run_id)
    store.finish_schedule_delivery(run_id, status=status, error=error, receipt=receipt)
    return {
        "delivery_status": status,
        "delivery_error": error,
        "delivery_receipt": receipt,
    }


async def drain_pending_deliveries() -> None:
    """Deliver saved finals without re-running their agent or repeating attachments."""
    from app.services.store import store

    for row in store.pending_schedule_deliveries():
        await _deliver_run(row)


async def fire_due_schedules(*, now: float | None = None) -> list[dict[str, Any]]:
    """Claim and fire all due schedules once (tests + APS safety sweep)."""
    from app.services.store import store

    ts = now if now is not None else time.time()
    await drain_pending_deliveries()
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


def notify_schedule_changed(schedule_id: str | None = None) -> None:
    """Re-sync APS after create/update/pause/resume/delete (best-effort)."""
    try:
        from app.scheduler.engine import is_running, sync_schedule

        if not is_running():
            return
        if not schedule_id:
            return
        sync_schedule(schedule_id)
    except Exception:
        logger.debug("notify_schedule_changed failed id=%s", schedule_id, exc_info=True)


def notify_schedule_removed(schedule_id: str) -> None:
    try:
        from app.scheduler.engine import is_running, remove_schedule

        if is_running():
            remove_schedule(schedule_id)
    except Exception:
        logger.debug("notify_schedule_removed failed id=%s", schedule_id, exc_info=True)


def start_scheduler(*, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    """Start APScheduler wake engine (idempotent). Called from app lifespan."""
    _ = poll_seconds  # retained for call-site compat; wake is event-driven now
    from app.scheduler.engine import start_engine

    start_engine()
    logger.info("scheduler started (APScheduler)")


async def stop_scheduler() -> None:
    """Stop APScheduler (idempotent)."""
    from app.scheduler.engine import stop_engine

    await stop_engine()
    logger.info("scheduler stopped")


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "MAX_PARALLEL_FIRES",
    "fire_schedule",
    "fire_due_schedules",
    "run_schedule_now",
    "notify_schedule_changed",
    "notify_schedule_removed",
    "start_scheduler",
    "stop_scheduler",
]

"""Completion admission for supervised processes, using the ordinary chat lease."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.store import store

logger = logging.getLogger(__name__)
_loop: asyncio.AbstractEventLoop | None = None
_drains: dict[str, asyncio.Task] = {}
_cards: dict[str, asyncio.Task] = {}
_card_dirty: set[str] = set()
_drain_dirty: set[str] = set()
_deliveries: set[asyncio.Task] = set()
_closed = True


def evidence(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep destinations/credentials out of the model's tool-result context."""
    fields = ("id", "command", "backend", "workplace_id", "status", "returncode",
              "started_at", "finished_at", "truncated", "agent_id")
    return [{**{key: job.get(key) for key in fields},
             "stdout": str(job.get("stdout") or "")[-4096:],
             "stderr": str(job.get("stderr") or "")[-4096:]} for job in jobs]


def on_job_update(job: dict[str, Any]) -> None:
    """Supervisor callbacks may originate in pipe-reader threads."""
    if _closed or _loop is None or _loop.is_closed():
        return
    _loop.call_soon_threadsafe(_updated, job)


def _updated(job: dict[str, Any]) -> None:
    if _closed:
        return
    sid = job["session_id"]
    from app.services.chat import notify_session_event

    notify_session_event(sid, "process", {"id": job["id"], "status": job["status"]})
    if job.get("delivery"):
        _card_dirty.add(job['id'])
    if job.get("delivery") and job["id"] not in _cards:
        async def refresh() -> None:
            try:
                from app.channels.telegram_jobs import update_card

                while job['id'] in _card_dirty:
                    _card_dirty.discard(job['id'])
                    await update_card(job["id"])
            except Exception:
                logger.exception("job card update failed job=%s", job["id"])
            finally:
                _cards.pop(job["id"], None)

        _cards[job["id"]] = asyncio.create_task(refresh())
    if job.get("continuation_status") == "pending":
        request_drain(sid)


def request_drain(session_id: str) -> None:
    if _closed or _loop is None or _loop.is_closed():
        return
    def schedule() -> None:
        if _closed:
            return
        if session_id in _drains:
            _drain_dirty.add(session_id)
            return
        task = asyncio.create_task(_drain(session_id), name=f"job-results:{session_id}")
        _drains[session_id] = task
        def done(_task):
            _drains.pop(session_id, None)
            if session_id in _drain_dirty:
                _drain_dirty.discard(session_id)
                request_drain(session_id)
        task.add_done_callback(done)
    _loop.call_soon_threadsafe(schedule)


def wake_pending() -> None:
    """Lease/dispatcher cleanup supplies the retry signal, without model polling."""
    if _closed:
        return
    for job in store.list_background_jobs(continuation_status='pending', include_logs=False):
        request_drain(job["session_id"])


def _block(jobs: list[dict[str, Any]], reason: str) -> None:
    for job in jobs:
        store.update_background_job(job["id"], {
            "continuation_status": "blocked", "delivery_status": "blocked",
            "error": reason,
        })


async def _drain(sid: str) -> None:
    from app.services.chat import SessionTurnBusy, get_active_session_turn, start_session_turn

    try:
        while not _closed and not store.background_jobs_paused(sid):
            pending = [j for j in store.list_background_jobs(sid, continuation_status='pending', limit=16)
                       if not j.get("monitoring_closed")]
            if not pending or get_active_session_turn(sid):
                return
            session = store.get_session(sid)
            coordinator = store.get_agent(session.get("coordinator_id", "")) if session else None
            if (not session or not coordinator or not coordinator.get("enabled")
                    or any(j["user_id"] != session["user_id"] for j in pending)):
                _block(pending, "Originating session/owner/coordinator is unavailable")
                return
            account = store.get_user(session["user_id"])
            if ((account and not account.get("enabled", True))
                    or (not account and session['user_id'].startswith('usr_'))):
                _block(pending, "Originating account is unavailable or disabled")
                return
            target = pending[0].get("delivery")
            batch = [j for j in pending if j.get("delivery") == target]
            if target:
                from app.channels.delivery import DeliveryBlocked
                from app.channels.telegram_delivery import _check_target
                from app.channels.telegram_jobs import admit_continuation

                try:
                    _check_target(target)
                except DeliveryBlocked as exc:
                    _block(batch, str(exc))
                    continue
                admit_continuation(batch)
                # Telegram owns its per-chat slot, user queue, UI, and delivery.
                return
            try:
                turn, queue = await start_session_turn(
                    sid, "", session["user_id"],
                    background_job_ids=[j["id"] for j in batch],
                )
            except SessionTurnBusy:
                return
            turn.unsubscribe(queue)
            if turn.task:
                await asyncio.shield(turn.task)
            # Accepted interactive input wins the next lease before another batch.
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("background continuation failed session=%s", sid)


def start() -> None:
    global _loop, _closed
    from app.services.background_jobs import manager

    _loop = asyncio.get_running_loop()
    _closed = False
    # Metadata recovery does not imply replaying partially executed model turns.
    recovered_finals: dict[str, list[dict]] = {}
    for job in store.list_background_jobs(include_logs=False):
        if job.get("continuation_status") in {"pending", "claimed"}:
            store.set_background_jobs_paused(job["session_id"], True)
            if job["continuation_status"] == "claimed":
                store.update_background_job(job["id"], {"continuation_status": "cancelled"})
        if job.get("delivery_status") == "sending":
            store.update_background_job(job["id"], {"delivery_status": "unknown"})
        if job.get('card_delivery_status') == 'sending':
            store.update_background_job(job['id'], {'card_delivery_status': 'unknown'})
        if job.get('continuation_status') == 'consumed' and job.get('delivery'):
            recovered_finals.setdefault(job.get('result_event_id') or job['id'], []).append(job)
    manager.startup(_loop, on_job_update)
    from app.channels.telegram_jobs import deliver_stored_final

    for batch in recovered_finals.values():
        pending = [job for job in batch if job['delivery_status'] == 'pending']
        if not pending:
            continue
        if len(pending) != len(batch):
            for job in pending:
                store.update_background_job(job['id'], {'delivery_status': 'unknown'})
            continue
        task = asyncio.create_task(deliver_stored_final([job['id'] for job in pending]))
        _deliveries.add(task)
        task.add_done_callback(_deliveries.discard)


async def stop() -> None:
    global _closed, _loop
    _closed = True
    tasks = list(_drains.values()) + list(_cards.values()) + list(_deliveries)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _drains.clear()
    _cards.clear()
    _drain_dirty.clear()
    _card_dirty.clear()
    _deliveries.clear()
    from app.services.background_jobs import manager

    await manager.shutdown()
    _loop = None

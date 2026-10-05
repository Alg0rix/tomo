"""Aggregate turn admission and confirmed asynchronous revocation teardown.

Startup must register stop_session in the composite stopper alongside container,
background and terminal backends. Cancelling an asyncio task is NOT confirmation
that a worker-thread/OS process stopped; those backends have separate stoppers.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.runtime.access import AccessUnavailable


@dataclass
class Turn:
    user_id: str
    session_id: str
    loop: asyncio.AbstractEventLoop
    task: asyncio.Task
    count: int = 1


_lock = threading.RLock()
_turns: dict[asyncio.Task, Turn] = {}


@asynccontextmanager
async def admitted_turn(context):
    from app.services import store

    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    assert task is not None

    def _prune_done():
        # A finished task can never still own a slot: fire-and-forget
        # work destroyed at loop teardown may never run its release.
        # Pruning here keeps one stranded registration from denying
        # later turns their aggregate quota indefinitely.
        for known in [t for t in _turns if t.done()]:
            _turns.pop(known, None)

    def admit():
        with store.access.execution_guard(context) as current:
            from app.runtime import ledger
            with _lock:
                _prune_done()
                # Nested same-turn admission (the turn pipeline layers each
                # admit: ingress wrapper plus the agent loop) shares the one
                # slot instead of consuming a second. This MUST precede the
                # aggregate check: the ledger correctly counts our own live
                # slot, so checking first would deny our own nesting whenever
                # any other unit (e.g. a background job) fills the quota.
                # Quota freshness still holds: any quota/role/grant mutation
                # bumps access_generation, which the guard above already
                # revalidated before this bypass can run.
                existing = _turns.get(task)
                if (existing is not None and existing.user_id == current.user_id
                        and existing.session_id == current.session_id):
                    existing.count += 1
                    return
            # First admission for this task: one slot of the user's shared
            # cross-backend quota (containers/host/background/terminals draw
            # from the same total via the ledger).
            ledger.check(current.user_id, current.quota)
            with _lock:
                _prune_done()
                if task in _turns:
                    # A concurrent admit for this task registered while the
                    # ledger check ran; share it instead of double-charging.
                    # Sharing after a passed check is safe: the check already
                    # accounted for every live slot including this one.
                    _turns[task].count += 1
                    return
                count = sum(t.user_id == context.user_id for t in _turns.values())
                if count >= context.quota.max_concurrent_jobs:
                    raise AccessUnavailable("User aggregate execution concurrency limit reached")
                _turns[task] = Turn(context.user_id, context.session_id, loop, task)

    # Never block the event loop on the policy/teardown fence.
    admission = asyncio.create_task(asyncio.to_thread(admit))
    try:
        await asyncio.shield(admission)
        # A turn spans multiple provider/tool calls and may legitimately take
        # longer than an individual process budget. Do not wall-clock cut it
        # for either Admin or Member. Stop/revocation still cancel this task;
        # provider/tool deadlines and backend resource limits remain separate.
        yield
    finally:
        def release(future):
            # Do not await a pending policy fence here: a mutation may itself
            # be waiting for this task's cancellation. Admission revalidates
            # the persistent generation/barrier before it can register work.
            if future.cancelled() or future.exception() is not None:
                return
            with _lock:
                turn = _turns.get(task)
                if turn:
                    turn.count -= 1
                    if turn.count == 0:
                        _turns.pop(task, None)
        if admission.done():
            release(admission)
        else:
            admission.add_done_callback(release)


def stop_session(session_id: str) -> None:
    """Synchronous confirmed task drain, called by the composite policy stopper."""
    with _lock:
        turns = [t for t in _turns.values() if t.session_id == session_id]
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    for turn in turns:
        if turn.task.done():
            continue
        turn.loop.call_soon_threadsafe(turn.task.cancel)
        if current_loop is turn.loop:
            # A sync control-plane call on this loop cannot wait for its own
            # cancellation. Keep persistent access_pending until a thread retry.
            raise AccessUnavailable("Execution cancellation is pending; retry teardown off the event loop")

        async def drain(task=turn.task):
            await asyncio.gather(task, return_exceptions=True)

        if turn.loop.is_closed():
            raise AccessUnavailable("Execution loop teardown could not be confirmed")
        try:
            asyncio.run_coroutine_threadsafe(drain(), turn.loop).result(timeout=10)
        except Exception as exc:
            raise AccessUnavailable("Execution cancellation could not be confirmed") from exc

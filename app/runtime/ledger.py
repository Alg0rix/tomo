"""Shared cross-backend aggregate admission ledger.

Every execution backend (interactive turns, chat containers, host processes,
background jobs, terminals) enforces the SAME per-user aggregate concurrency
limit through this module instead of keeping independent counters that each
imply a separate aggregate limit.

Counting rules (documented, enforced, tested):

- Attribution is by owner: delegated, subagent and background work counts
  toward the owning ``user_id`` taken from the bound execution context, never
  toward a delegatee or a service account.
- Synchronous nested execution shares its admission slot instead of being
  double counted: a container ``exec`` or host spawn issued inside an already
  admitted turn for the same session does not consume a second slot. The
  snapshot below implements this by not counting container/host handles whose
  session currently holds a live admitted turn.
- Durable work always counts separately: queued container jobs, background
  supervisor rows and terminals outlive any single turn, so each holds its own
  slot even while its originating turn is still open. The transient overlap is
  counted conservatively (fail-closed direction) and documented here.
- Container-backed background rows are counted once via their live container
  reservation, not via both the container and the background row.
- Host processes supervised by the terminal or background managers are
  counted once via their manager's slot (live-PID exclusion), not via both
  the manager and the host table.
- A spawn issued synchronously while setting up an already-admitted durable
  unit (``spawning_scope``, set by the background manager around process
  creation) shares that admission instead of being charged a second slot
  for the same unit of work.

The ledger holds no registrations of its own: usage is recomputed from the
live backend registries on every check, so a failed or crashed admission
cannot strand a phantom slot. Registries are snapshotted without acquiring
backend locks (GIL-atomic dict copies), so the ledger can be consulted while
a backend holds its own admission lock without introducing lock ordering.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.runtime.access import AccessUnavailable

# Marks synchronous setup nested inside an already-admitted durable unit
# (currently: the background manager's spawn, which runs in the same thread
# as its aggregate admission). Spawns covered by the scope share the outer
# admission instead of consuming a second slot for the same unit of work.
_spawning: ContextVar[tuple[str, str] | None] = ContextVar("ledger_spawning", default=None)


@contextmanager
def spawning_scope(user_id: str, kind: str) -> Iterator[None]:
    """Share the surrounding durable admission with synchronous spawns."""
    token = _spawning.set((user_id, kind))
    try:
        yield
    finally:
        _spawning.reset(token)


def covered_by_scope(user_id: str) -> bool:
    """True when this thread is setting up an already-admitted unit."""
    scope = _spawning.get()
    return scope is not None and scope[0] == user_id


def _supervised_pids() -> set[int]:
    """Live PIDs already covered by a durable manager slot.

    Terminal shells and background-supervised processes are registered in
    BOTH their manager and the host process table. Their manager's slot
    (terminal count / background row) is the single aggregate charge; the
    host row must not add a second one.
    """
    pids: set[int] = set()
    try:
        from app.services.terminals import terminal_manager

        for term in dict(terminal_manager.terminals).values():
            proc = getattr(term, "process", None)
            try:
                if proc is not None and proc.poll() is None:
                    pids.add(proc.pid)
            except Exception:
                pass
    except Exception:
        pass
    try:
        from app.services.background_jobs import manager

        for state in dict(manager._local).values():
            proc = getattr(state, "proc", None)
            try:
                if proc is not None and proc.poll() is None:
                    pids.add(proc.pid)
            except Exception:
                pass
    except Exception:
        pass
    return pids


def snapshot(user_id: str) -> dict:
    """Live aggregate usage for ``user_id`` across all execution backends."""
    from app.runtime import supervision
    from app.runtime.isolation.backend import backend as container_backend
    from app.runtime.isolation import host

    turns = [t for t in dict(supervision._turns).values() if t.user_id == user_id]
    turn_sessions = {t.session_id for t in turns}
    containers = [
        e for e in dict(container_backend._environments).values()
        if e.context.user_id == user_id and e.busy and e.context.session_id not in turn_sessions
    ]
    covered = _supervised_pids()
    procs = 0
    for sid, owned in dict(host._processes).items():
        if sid in turn_sessions:
            continue
        for proc in list(owned):
            if getattr(proc, "tomo_user_id", None) != user_id:
                continue
            try:
                alive = proc.poll() is None
            except Exception:
                alive = False
            if alive and proc.pid not in covered:
                procs += 1
    background = 0
    try:
        from app.services import store

        for job in store.list_background_jobs():
            if job.get("user_id") != user_id or job.get("status") not in ("starting", "running", "stopping"):
                continue
            if job.get("backend") == "container":
                continue  # counted once via the live container reservation above
            background += 1
    except Exception:
        # The store is the admission authority's own database; if it cannot be
        # read here, the surrounding admission fence fails closed on its own
        # revalidation. Count only what is locally visible.
        pass
    terminals = 0
    try:
        from app.services.terminals import terminal_manager

        terminals = sum(
            1 for t in dict(terminal_manager.terminals).values()
            if getattr(getattr(t, "execution", None), "user_id", None) == user_id
        )
    except Exception:
        pass
    total = len(turns) + len(containers) + procs + background + terminals
    return {
        "turns": len(turns),
        "containers": len(containers),
        "host_processes": procs,
        "background_jobs": background,
        "terminals": terminals,
        "total": total,
    }


def check(user_id: str, quota, *, within_session: str | None = None) -> dict:
    """Deny when ``user_id`` already holds its full aggregate quota.

    ``quota`` must be the caller backend's already-revalidated execution
    quota (never resolved here against the global singleton: some callers
    bind contexts from their own policy/store). Freshness is guaranteed
    because every quota/role/grant mutation bumps ``access_generation`` and
    invalidates previously bound contexts at the surrounding admission
    fence. Returns the admitting usage snapshot (for audit/tests).

    ``within_session`` marks synchronous nested execution: work issued
    inside an already admitted turn for that session shares the turn's slot
    instead of consuming a second one. Durable admissions that outlive the
    turn (queued container jobs, background rows, terminals) must NOT pass
    ``within_session``; they always consume their own slot.
    """
    usage = snapshot(user_id)
    total = usage["total"]
    if within_session is not None:
        from app.runtime import supervision

        nested = sum(
            1 for t in dict(supervision._turns).values()
            if t.user_id == user_id and t.session_id == within_session
        )
        total -= nested
    if total >= quota.max_concurrent_jobs:
        raise AccessUnavailable("User aggregate execution concurrency limit reached")
    return usage

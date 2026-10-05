"""Explicit authorized cross-machine transfers (Stage 4).

A transfer moves bytes from a source endpoint to a destination endpoint
through coordinator staging. Both endpoints must be enabled in the calling
chat (grants + activation); the source needs read permission and the
destination needs write permission. Cross-machine endpoints are
transfer-only resources: they are never mounted anywhere.

Enforced per transfer:

* current execution identity (no anonymous transfers);
* source read + destination write against current grants (revalidated, so
  revocation denies queued/new work);
* a bounded total (``MAX_TRANSFER_BYTES``) streamed in chunks with progress;
* ``.part`` staging cleanup on failure/cancel — no orphaned partial files;
* audit with provenance (actor, session, agent, endpoints, bytes, outcome);
* no whole-machine mounts: endpoints are single authorized locations.

Portal staging (``/_portal/<name>/...``) is coordinator storage with a
shared namespace, not a confidentiality boundary: never stage secrets or
private keys (use the session-scoped secret broker). Transfer jobs are
owner-attributed; status/cancel observe only the caller's own jobs.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.runtime.access import current_execution
from app.runtime.portal.io import (
    DEFAULT_CHUNK,
    Location,
    copy_sync,
    parse_location,
)

_logger = logging.getLogger(__name__)

# Sync under this size; larger copies become background jobs.
SYNC_MAX_BYTES = 512 * 1024

#: Hard cap for one transfer. Larger requests refuse with a clear error
#: instead of spooling unbounded bytes through the coordinator.
MAX_TRANSFER_BYTES = 256 * 1024 * 1024


@dataclass
class TransferJob:
    id: str
    src: str
    dst: str
    agent_id: str | None
    owner_user_id: str
    session_id: str
    started_at: float
    bytes_done: int = 0
    total_bytes: int = 0
    status: str = "running"  # running | done | error | cancelled
    error: str = ""
    finished_at: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pct = 0.0
            if self.total_bytes > 0:
                pct = min(100.0, 100.0 * self.bytes_done / self.total_bytes)
            return {
                "id": self.id,
                "src": self.src,
                "dst": self.dst,
                "agent_id": self.agent_id or "",
                "owner_user_id": self.owner_user_id,
                "session_id": self.session_id,
                "status": self.status,
                "bytes_done": self.bytes_done,
                "total_bytes": self.total_bytes,
                "percent": round(pct, 1),
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


_lock = threading.Lock()
_jobs: dict[str, TransferJob] = {}
_counter = itertools.count(1)


def reset() -> None:
    """Test helper."""
    with _lock:
        for job in _jobs.values():
            job._cancel.set()
        _jobs.clear()


def _current_owner() -> tuple[str, str, str | None]:
    from app.services import store

    context = store.access.revalidate(current_execution())
    return context.user_id, context.session_id, context.agent_id


def _audit(action: str, *, session_id: str, agent_id: str | None,
           destination_id: str, outcome: str, job_id: str = "") -> None:
    from app.services import store

    context = store.access.revalidate(current_execution())
    store.access.audit(
        context.user_id, action, session_id=session_id,
        agent_id=agent_id or "", destination_id=destination_id,
        outcome=outcome, job_id=job_id,
    )


def get(job_id: str) -> TransferJob | None:
    owner, _, _ = _current_owner()
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.owner_user_id != owner:
        return None
    return job


def list_jobs(*, agent_id: str | None = None) -> list[TransferJob]:
    owner, _, _ = _current_owner()
    with _lock:
        jobs = [j for j in _jobs.values() if j.owner_user_id == owner]
    if agent_id:
        jobs = [j for j in jobs if j.agent_id == agent_id]
    return jobs


def cancel(job_id: str) -> TransferJob | None:
    job = get(job_id)
    if job is None:
        return None
    job._cancel.set()
    with job._lock:
        if job.status == "running":
            job.status = "cancelled"
            job.finished_at = time.time()
    _cleanup_part(job.dst)
    return job


def _cleanup_part(dst_spec: str) -> None:
    """Remove a leftover ``.part`` staging file for a failed/cancelled write."""
    try:
        loc = _locate(dst_spec, write=False)
    except Exception:
        return
    try:
        if loc.kind == "portal":
            from app.runtime.portal.paths import resolve_portal_fs

            part = Path(str(resolve_portal_fs(loc.path)) + ".part")
        elif loc.kind == "local":
            from app.runtime.portal.io import _jail_local

            assert loc.workplace is not None
            part = Path(str(_jail_local(loc.workplace, loc.path)) + ".part")
        else:
            return
        try:
            part.unlink()
        except FileNotFoundError:
            pass
    except Exception:
        pass


def _locate(spec: str, *, write: bool) -> Location:
    loc = parse_location(spec)
    from app.runtime.portal.io import _authorize_location

    _authorize_location(loc, write=write)
    return loc


def start_transfer(
    src_spec: str,
    dst_spec: str,
    *,
    agent_id: str | None = None,
    force_async: bool = False,
) -> dict[str, Any]:
    """Copy ``src`` → ``dst``. Small files sync; large ones return a job id."""
    from app.runtime.access import AccessDenied

    owner, session_id, bound_agent = _current_owner()
    agent = agent_id or bound_agent
    src = _locate(src_spec, write=False)
    dst = _locate(dst_spec, write=True)
    if src.kind == "portal" and dst.kind == "portal" and src.path == dst.path:
        raise ValueError("source and destination are the same file")

    from app.runtime.portal.io import stat_size

    try:
        total = stat_size(src)
    except FileNotFoundError:
        _audit("portal.transfer", session_id=session_id, agent_id=agent,
               destination_id=dst.label, outcome="not-found")
        raise
    if total > MAX_TRANSFER_BYTES:
        _audit("portal.transfer", session_id=session_id, agent_id=agent,
               destination_id=dst.label, outcome="too-large")
        raise AccessDenied(
            f"Transfer of {total} bytes exceeds the {MAX_TRANSFER_BYTES}-byte bound"
        )

    if total <= SYNC_MAX_BYTES and not force_async:
        started = time.time()
        try:
            written = copy_sync(src, dst)
        except Exception:
            _cleanup_part(dst_spec)
            _audit("portal.transfer", session_id=session_id, agent_id=agent,
                   destination_id=dst.label, outcome="error")
            raise
        _audit("portal.transfer", session_id=session_id, agent_id=agent,
               destination_id=dst.label, outcome="ok")
        _logger.info(
            "portal transfer ok user=%s session=%s bytes=%d src=%s dst=%s %.2fs",
            owner, session_id, written, src.label, dst.label, time.time() - started,
        )
        return {
            "mode": "sync",
            "bytes": written,
            "src": src.label,
            "dst": dst.label,
            "status": "done",
        }

    with _lock:
        job_id = f"xfer_{next(_counter)}"
        job = TransferJob(
            id=job_id,
            src=src.label,
            dst=dst.label,
            agent_id=agent,
            owner_user_id=owner,
            session_id=session_id,
            started_at=time.time(),
            total_bytes=total,
        )
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, src_spec, dst_spec, owner, session_id, agent),
        name=f"portal-{job_id}",
        daemon=True,
    )
    thread.start()
    _audit("portal.transfer", session_id=session_id, agent_id=agent,
           destination_id=dst.label, outcome="started", job_id=job_id)
    return {
        "mode": "async",
        "id": job_id,
        "bytes": 0,
        "total_bytes": total,
        "src": src.label,
        "dst": dst.label,
        "status": "running",
    }


def _run_job(job: TransferJob, src_spec: str, dst_spec: str,
             owner: str, session_id: str, agent: str | None) -> None:
    from app.runtime.access import bind_execution
    from app.services import store

    started = time.time()
    try:
        # Re-resolve the owner's current ceiling on the worker thread: queued
        # work uses current permissions, never a cached grant. Revocation
        # denies here instead of copying with stale rights.
        context = store.access.resolve_context(owner, job.session_id)
        token = bind_execution(context)
        try:
            src = _locate(src_spec, write=False)
            dst = _locate(dst_spec, write=True)

            def on_progress(done: int, total: int) -> None:
                if job._cancel.is_set():
                    raise RuntimeError("cancelled")
                with job._lock:
                    job.bytes_done = done
                    job.total_bytes = total

            # The binding stays held for the whole copy so every chunk
            # revalidates against current grants; revocation mid-transfer
            # denies the remaining chunks instead of finishing stale.
            written = copy_sync(src, dst, chunk_size=DEFAULT_CHUNK, on_progress=on_progress)
        finally:
            from app.runtime.access import reset_execution

            reset_execution(token)
        with job._lock:
            if job.status == "cancelled":
                _cleanup_part(dst_spec)
                return
            job.bytes_done = written
            job.status = "done"
            job.finished_at = time.time()
        _logger.info(
            "portal transfer done user=%s session=%s job=%s bytes=%d %.2fs",
            owner, session_id, job.id, written, time.time() - started,
        )
    except Exception as exc:
        _logger.warning("portal transfer %s failed: %s", job.id, exc, exc_info=True)
        _cleanup_part(dst_spec)
        with job._lock:
            if job.status != "cancelled":
                job.status = "error"
                job.error = str(exc)[:300]
            job.finished_at = time.time()


__all__ = [
    "SYNC_MAX_BYTES",
    "MAX_TRANSFER_BYTES",
    "TransferJob",
    "reset",
    "get",
    "list_jobs",
    "cancel",
    "start_transfer",
]

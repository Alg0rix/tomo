"""Background portal transfer jobs with byte progress."""

from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.runtime.portal.io import (
    DEFAULT_CHUNK,
    Location,
    copy_sync,
    parse_location,
)

_logger = logging.getLogger(__name__)

# Sync under this size; larger copies become background jobs.
SYNC_MAX_BYTES = 512 * 1024


@dataclass
class TransferJob:
    id: str
    src: str
    dst: str
    agent_id: str | None
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


def get(job_id: str) -> TransferJob | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs(*, agent_id: str | None = None) -> list[TransferJob]:
    with _lock:
        jobs = list(_jobs.values())
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
    return job


def start_transfer(
    src_spec: str,
    dst_spec: str,
    *,
    agent_id: str | None = None,
    force_async: bool = False,
) -> dict[str, Any]:
    """Copy ``src`` → ``dst``. Small files sync; large ones return a job id."""
    src = parse_location(src_spec)
    dst = parse_location(dst_spec)

    # Probe size (may fail for missing dst — that's ok; we need src size).
    from app.runtime.portal.io import stat_size

    total = stat_size(src)
    if total <= SYNC_MAX_BYTES and not force_async:
        written = copy_sync(src, dst)
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
            agent_id=agent_id,
            started_at=time.time(),
            total_bytes=total,
        )
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, src, dst),
        name=f"portal-{job_id}",
        daemon=True,
    )
    thread.start()
    return {
        "mode": "async",
        "id": job_id,
        "bytes": 0,
        "total_bytes": total,
        "src": src.label,
        "dst": dst.label,
        "status": "running",
    }


def _run_job(job: TransferJob, src: Location, dst: Location) -> None:
    try:

        def on_progress(done: int, total: int) -> None:
            if job._cancel.is_set():
                raise RuntimeError("cancelled")
            with job._lock:
                job.bytes_done = done
                job.total_bytes = total

        written = copy_sync(src, dst, chunk_size=DEFAULT_CHUNK, on_progress=on_progress)
        with job._lock:
            if job.status == "cancelled":
                return
            job.bytes_done = written
            job.status = "done"
            job.finished_at = time.time()
    except Exception as exc:
        _logger.warning("portal transfer %s failed: %s", job.id, exc)
        with job._lock:
            if job.status != "cancelled":
                job.status = "error"
                job.error = str(exc)
            job.finished_at = time.time()


__all__ = [
    "SYNC_MAX_BYTES",
    "TransferJob",
    "reset",
    "get",
    "list_jobs",
    "cancel",
    "start_transfer",
]

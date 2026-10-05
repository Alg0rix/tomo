"""In-process durable-job adapter for the existing background supervisor.

After coordinator restart, broker orphan cleanup kills containers; interrupted
jobs must not be silently restarted. Persist the immutable context in the job.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from app.runtime.access import AccessDenied, ExecutionContext
from .backend import backend


@dataclass
class Job:
    context: ExecutionContext
    status: str = "starting"
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

_jobs: dict[str, Job] = {}
_lock = threading.RLock()


def start(context: ExecutionContext, command: str, cwd: str) -> dict:
    with backend.access.execution_guard(context) as current:
        current = backend.access.require_tool(current, "bash")
        backend._destination(current)
        handle = "container_" + uuid.uuid4().hex
        job = Job(current)
        backend.reserve_job(current, handle)
        def run():
            try:
                result = backend.execute(current, ["bash", "-lc", command], cwd=cwd,
                                         timeout=current.quota.duration_seconds, cancel_event=job.cancel,
                                         job_id=handle)
                with _lock:
                    job.stdout, job.stderr, job.exit_code = result.stdout, result.stderr, result.returncode
                    job.status = "stopped" if job.cancel.is_set() else "succeeded" if result.returncode == 0 else "failed"
            except Exception as exc:
                # Revalidation can fail before execute reaches its finally. A
                # queued job must not leave reserved mounts/capacity behind.
                try:
                    backend.stop_job(current.session_id, handle)
                except AccessDenied:
                    with _lock:
                        job.status = "unknown"
                        job.stderr = "Selected container job teardown is unconfirmed"
                    return
                with _lock:
                    job.stderr = str(exc) if isinstance(exc, AccessDenied) else "Selected container job failed"
                    job.status, job.exit_code = ("stopped" if job.cancel.is_set() else "failed"), -1
        with _lock:
            _jobs[handle] = job
            job.thread = threading.Thread(target=run, daemon=True, name=handle)
            try:
                job.thread.start()
            except Exception:
                del _jobs[handle]
                backend.stop_job(current.session_id, handle)
                raise
    return observe(handle, context=context)


def observe(handle: str, *, context: ExecutionContext | None = None) -> dict:
    from app.runtime.access import current_execution

    current = backend.access.revalidate(context or current_execution())
    with _lock:
        job = _jobs.get(handle)
        if job and (current.user_id, current.session_id) != (job.context.user_id, job.context.session_id):
            raise AccessDenied("Container job is unavailable")
        return _snapshot(handle)


def _snapshot(handle: str) -> dict:
    """Trusted supervisor snapshot; public observations require current identity."""
    with _lock:
        job = _jobs.get(handle)
        if not job:
            return {"id": handle, "backend_handle": handle, "status": "interrupted", "exit_code": None,
                    "reason": "Container supervisor restarted; execution was not resumed"}
        return {"id": handle, "backend_handle": handle, "status": job.status, "exit_code": job.exit_code,
                "stdout": job.stdout, "stderr": job.stderr, "stop_confirmed": job.status == "stopped"}


def stop(handle: str) -> dict:
    with _lock:
        job = _jobs.get(handle)
        if not job:
            raise AccessDenied("Container job teardown requires supervisor recovery")
        if job.status in {"succeeded", "failed", "stopped", "interrupted"}:
            return _snapshot(handle)
        job.cancel.set()
    backend.stop_job(job.context.session_id, handle)
    with _lock:
        job.status, job.exit_code = "stopped", -1
    # Thread can be queued on the policy fence. Cancellation is admission-
    # fenced, and container removal confirms all already-admitted work stopped.
    return _snapshot(handle)


def stop_session(session_id: str):
    with _lock:
        handles = [handle for handle, job in _jobs.items()
                   if job.context.session_id == session_id and job.thread.is_alive()]
        for handle in handles:
            _jobs[handle].cancel.set()
    backend.stop_session(session_id)
    # A queued thread can be waiting on the policy mutation fence held by
    # this callback. Do not join it here: cancellation is checked INSIDE the
    # backend admission lock and all already-admitted containers are gone.

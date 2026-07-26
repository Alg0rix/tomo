"""In-process registry for background bash jobs started by the ``bash`` tool."""

from __future__ import annotations

import itertools
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackgroundJob:
    id: str
    command: str
    agent_id: str | None
    started_at: float
    process: subprocess.Popen[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def poll(self) -> None:
        """Harvest exit status / output if the process has finished."""
        with self._lock:
            if self.returncode is not None:
                return
            code = self.process.poll()
            if code is None:
                return
            self.returncode = code
            try:
                out, err = self.process.communicate(timeout=0.1)
            except Exception:
                out, err = "", ""
            self.stdout = out or ""
            self.stderr = err or ""

    @property
    def status(self) -> str:
        self.poll()
        if self.returncode is None:
            return "running"
        return "exited"


_lock = threading.Lock()
_jobs: dict[str, BackgroundJob] = {}
_counter = itertools.count(1)


def reset() -> None:
    """Drop all jobs (test helper). Does not kill processes."""
    with _lock:
        _jobs.clear()


def register(
    command: str,
    process: subprocess.Popen[str],
    *,
    agent_id: str | None = None,
) -> BackgroundJob:
    """Register a running subprocess and return its job record."""
    with _lock:
        job_id = f"job_{next(_counter)}"
        job = BackgroundJob(
            id=job_id,
            command=command,
            agent_id=agent_id,
            started_at=time.time(),
            process=process,
        )
        _jobs[job_id] = job
        return job


def get(job_id: str) -> BackgroundJob | None:
    with _lock:
        job = _jobs.get(job_id)
    if job is not None:
        job.poll()
    return job


def list_jobs() -> list[BackgroundJob]:
    with _lock:
        jobs = list(_jobs.values())
    for job in jobs:
        job.poll()
    return jobs


def kill(job_id: str) -> BackgroundJob | None:
    job = get(job_id)
    if job is None:
        return None
    with job._lock:
        if job.returncode is None:
            try:
                job.process.kill()
            except OSError:
                pass
            try:
                out, err = job.process.communicate(timeout=2)
            except Exception:
                out, err = "", ""
            job.returncode = job.process.returncode
            if out:
                job.stdout = out
            if err:
                job.stderr = err
    return job


def job_summary(job: BackgroundJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "command": job.command,
        "status": job.status,
        "returncode": job.returncode,
        "started_at": job.started_at,
        "agent_id": job.agent_id,
    }


__all__ = [
    "BackgroundJob",
    "register",
    "get",
    "list_jobs",
    "kill",
    "reset",
    "job_summary",
]

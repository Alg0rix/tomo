"""Supervision for explicitly activated unrestricted LOCAL execution.

This is NOT confinement. A hostile unrestricted child can escape process-group
supervision or affect the host. Use only when that warning was accepted.

Unrestricted execution still draws from the shared per-user aggregate ledger
(see app/runtime/ledger.py) and, where the host provides it, real OS
resource ceilings via ``prlimit(1)``. When ``prlimit`` is unavailable the
per-process OS ceiling is unenforced and ``quota_status()`` reports it;
concurrency, duration supervision and disk admission still apply. Only
explicitly unrestricted local destinations reach this backend.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading

from app.runtime.access import AccessDenied, current_execution

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_processes: dict[str, set] = {}


def quota_status() -> dict:
    """Host OS quota capability report (operator provisioning signal).

    ``prlimit_enforced`` tells whether per-process CPU/address-space ceilings
    below are real OS limits. When False, unrestricted host children are
    bounded only by aggregate concurrency, duration supervision and disk
    admission; the operator action is installing ``util-linux`` (``prlimit``)
    or moving restricted work to the cgroup-capable container backend.
    """
    import os as _os

    prlimit = shutil.which("prlimit")
    if _os.environ.get("TOMO_HOST_PRLIMIT") == "0":
        prlimit = None
    controllers: set[str] = set()
    try:
        controllers = set((open("/sys/fs/cgroup/cgroup.controllers").read().split()))
    except OSError:
        pass
    return {
        "prlimit_enforced": bool(prlimit),
        "prlimit_path": prlimit or "",
        "cgroup_controllers": sorted(controllers),
        "cgroup_cpu_memory": {"cpu", "memory"} <= controllers,
    }


def _prlimit_prefix(memory_mb: int, cpu_seconds: int) -> list[str]:
    status = quota_status()
    if not status["prlimit_enforced"]:
        _logger.warning("Host OS process ceilings unenforced: prlimit unavailable")
        return []
    # RLIMIT_AS bounds address space (bytes); RLIMIT_CPU bounds CPU seconds.
    # Killed children report SIGKILL/SIGXCPU, which supervisors already map
    # to failed/unknown rather than silent success.
    return [status["prlimit_path"], f"--as={max(1, memory_mb) * 1024 * 1024}",
            f"--cpu={max(1, cpu_seconds)}", "--"]


def popen(argv, **kwargs):
    from app.services.access import access

    context = current_execution()
    with access.execution_guard(context) as current:
        if current.execution_mode != "unrestricted" or any(r.kind != "local" for r in current.resources):
            raise AccessDenied("Host execution requires an explicitly unrestricted local destination")
        with _lock:
            # Shared aggregate ledger: synchronous spawns inside the caller's
            # admitted turn share its slot; spawns issued while setting up
            # an already-admitted durable unit (background manager) share
            # that admission. Other durable admissions are enforced by the
            # background/container/terminal managers that outlive turns.
            from app.runtime import ledger
            if not ledger.covered_by_scope(current.user_id):
                ledger.check(current.user_id, current.quota, within_session=current.session_id)
            kwargs["start_new_session"] = True
            prefix = _prlimit_prefix(current.quota.memory_mb, current.quota.duration_seconds)
            proc = subprocess.Popen([*prefix, *argv], **kwargs)
            proc.tomo_user_id = current.user_id
            _processes.setdefault(current.session_id, set()).add(proc)
            return proc


def forget(proc):
    # Always clean the process group, even after its parent exited.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)
    with _lock:
        for sid, processes in list(_processes.items()):
            processes.discard(proc)
            if not processes:
                del _processes[sid]


def stop_session(session_id: str):
    with _lock:
        for proc in list(_processes.get(session_id, ())):
            forget(proc)

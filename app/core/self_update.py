"""Self-update for script (systemd-user) installs — not containers or dev clones."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import REPO_ROOT

logger = logging.getLogger(__name__)

_CGROUP_HINTS = ("docker", "containerd", "kubepods", "lxc", "podman", "libpod", "nspawn")
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_spawn_lock = threading.Lock()
_spawned_at: float | None = None
# If a spawned update dies before restarting the service, allow a retry
# once the lockout window passes instead of 409ing forever.
_SPAWN_LOCKOUT_SECONDS = 600.0


def _update_in_flight() -> bool:
    return (
        _spawned_at is not None
        and (time.monotonic() - _spawned_at) < _SPAWN_LOCKOUT_SECONDS
    )


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("tomo")
    except Exception:
        try:
            from app import __version__

            return str(__version__)
        except Exception:
            return ""


def in_container() -> bool:
    if os.environ.get("TOMO_IN_CONTAINER", "").strip().lower() in _TRUTHY:
        return True
    if os.environ.get("container", "").strip():
        return True
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    for path in (Path("/proc/1/cgroup"), Path("/proc/self/cgroup")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(hint in text for hint in _CGROUP_HINTS):
            return True
    return False


def is_managed_git_install(*, home: Path | None = None) -> bool:
    from cli.paths import install_dir

    managed = install_dir(home).resolve()
    if not (managed / ".git").is_dir():
        return False
    try:
        return REPO_ROOT.resolve() == managed
    except OSError:
        return False


def can_self_update(*, home: Path | None = None) -> tuple[bool, str]:
    if in_container():
        return False, "container"
    if not is_managed_git_install(home=home):
        return False, "not_script_install"
    return True, "ok"


def install_kind(*, home: Path | None = None) -> str:
    if in_container():
        return "container"
    if is_managed_git_install(home=home):
        return "script"
    return "dev"


def _app_dir(home: Path | None = None) -> Path:
    from cli.paths import install_dir

    return install_dir(home) if is_managed_git_install(home=home) else REPO_ROOT


def _git_head(home: Path | None = None) -> str:
    from cli.git_sync import local_head

    app = _app_dir(home)
    if not (app / ".git").is_dir():
        return ""
    try:
        return local_head(app)
    except Exception:
        return ""


def status(*, home: Path | None = None) -> dict[str, Any]:
    ok, reason = can_self_update(home=home)
    from cli.paths import read_tracked_branch

    branch = read_tracked_branch(_app_dir(home))
    return {
        "can_update": ok,
        "reason": reason,
        "kind": install_kind(home=home),
        "version": package_version(),
        "head": _git_head(home=home) or None,
        "branch": branch,
        "commits_behind": None,
        "remote_head": None,
        "updating": _update_in_flight(),
    }


def check(*, home: Path | None = None) -> dict[str, Any]:
    ok, reason = can_self_update(home=home)
    out = status(home=home)
    if not ok:
        raise PermissionError(reason)
    from cli.git_sync import peek_origin
    from cli.paths import read_tracked_branch

    app = _app_dir(home)
    branch = read_tracked_branch(app)
    try:
        peek = peek_origin(app, branch)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc) or "git fetch failed") from exc
    out["head"] = peek.head
    out["remote_head"] = peek.remote_head
    out["commits_behind"] = peek.commits_behind
    out["branch"] = peek.branch
    return out


def _update_argv() -> list[str]:
    return [sys.executable, "-m", "cli", "update", "-y"]


def _spawn_via_systemd(argv: list[str], cwd: Path) -> bool:
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        return False
    proc = subprocess.run(
        [
            systemd_run,
            "--user",
            "--collect",
            "--quiet",
            f"--working-directory={cwd}",
            "--",
            *argv,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        logger.warning("systemd-run self-update failed: %s", err)
        return False
    return True


def _spawn_detached(argv: list[str], cwd: Path) -> None:
    from app.core.config import VAR_DIR

    VAR_DIR.mkdir(parents=True, exist_ok=True)
    log_path = VAR_DIR / "self-update.log"
    log_f = open(log_path, "ab")
    try:
        subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )
    finally:
        log_f.close()


def start_update(*, home: Path | None = None) -> None:
    """Kick off ``tomo update -y`` out of process so a service restart can complete."""
    global _spawned_at
    ok, reason = can_self_update(home=home)
    if not ok:
        raise PermissionError(reason)

    cwd = _app_dir(home)
    argv = _update_argv()

    with _spawn_lock:
        if _update_in_flight():
            raise RuntimeError("update already started")
        if _spawn_via_systemd(argv, cwd):
            _spawned_at = time.monotonic()
            return
        try:
            _spawn_detached(argv, cwd)
        except OSError as exc:
            raise RuntimeError(f"could not start update: {exc}") from exc
        _spawned_at = time.monotonic()

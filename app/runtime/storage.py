"""Aggregate account-private artifact/vault write admission (trusted service I/O).

Per-user disk admission is enforced in two layers:

1. ``private_write`` — synchronous trusted writes (artifacts, vault pages).
   Holds the per-user mutex across measure-and-write, so parallel writers
   for the same account serialize instead of racing past the quota.
2. ``reserve``/``release`` — transient byte reservations for HTTP uploads,
   whose bytes land on disk outside ``private_write``. Reserve before
   writing the file, release after success (bytes are then counted in the
   next ``used`` scan) or on failure/unlink. A failed write never strands a
   reservation; the failed path must release in ``finally``.

These are logical byte counters, NOT OS-hard quotas: they bound what Tomo
itself writes per account, but only a bounded backing volume (see
``managed_storage_status`` and ``deployment/sandbox/provision-storage.sh``)
provides physical fairness. Operators must provision the volume; Tomo
refuses restricted execution when the backing filesystem exceeds quota
instead of pretending the counter is a disk quota.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import threading

from app.runtime.access import AccessDenied, AccessUnavailable, current_execution

_locks: dict[str, threading.RLock] = {}
_lock = threading.Lock()
_reserved: dict[str, int] = {}


def _size(root: Path) -> int:
    total = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            path = Path(base) / name
            if not path.is_symlink():
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    pass
    return total


def _user_roots(user_id: str, *, home_root=None) -> set[Path]:
    """Every on-disk tree Tomo writes for ``user_id`` outside containers."""
    from app.core.config import TOMO_HOME
    from app.services import store
    from app.runtime.artifacts.fs import artifacts_dir
    from app.runtime.memory.vault.paths import vault_root

    roots = {vault_root(user_id, home_root=home_root)}
    sessions = store.list_sessions(user_id=user_id)
    roots.update(artifacts_dir(s['id'], home_root=home_root) for s in sessions)
    base = Path(TOMO_HOME if home_root is None else home_root) / "attachments"
    roots.update(base / s['id'] for s in sessions)
    roots.update(Path(w['root_path']) for w in store.list_workplaces()
                 if w.get('owner_user_id') == user_id and w.get('kind') == 'local'
                 and w.get('storage_kind') in {'personal', 'project'})
    return roots


def reserved_bytes(user_id: str) -> int:
    with _lock:
        return _reserved.get(user_id, 0)


def reserve(user_id: str, nbytes: int, quota=None) -> None:
    """Transiently hold ``nbytes`` of ``user_id``'s disk quota.

    Concurrency-safe: measure (used + already reserved) and register under
    one mutex, so parallel uploads cannot each pass admission and then
    jointly exceed the quota. Pair every ``reserve`` with ``release`` in a
    ``finally``; see module docstring.
    """
    from app.services import store

    if nbytes < 0:
        raise AccessDenied("Invalid private storage admission")
    account = store.access.require_user(user_id)
    del account
    current = quota or store.access.get_quota(user_id)
    with _lock:
        mutex = _locks.setdefault(user_id, threading.RLock())
    with mutex:
        with _lock:
            pending = _reserved.get(user_id, 0)
        used = sum(_size(root) for root in _user_roots(user_id))
        if used + pending + nbytes > current.disk_mb * 1024 * 1024:
            raise AccessUnavailable("User aggregate private storage quota reached")
        with _lock:
            _reserved[user_id] = pending + nbytes


def release(user_id: str, nbytes: int) -> None:
    with _lock:
        _reserved[user_id] = max(0, _reserved.get(user_id, 0) - max(0, nbytes))


@contextmanager
def private_write(extra_bytes: int, *, home_root=None):
    from app.services import store

    context = current_execution(required=False)
    if context is None:
        yield  # Trusted HTTP primitives must authorize their caller separately.
        return
    context = store.access.revalidate(context)
    if extra_bytes < 0:
        raise AccessDenied("Invalid private storage admission")
    with _lock:
        mutex = _locks.setdefault(context.user_id, threading.RLock())
    roots = set(_user_roots(context.user_id, home_root=home_root))
    roots.update(Path(r.root_path) for r in context.resources if r.kind == 'local')
    with mutex:
        used = sum(_size(root) for root in roots)
        pending = reserved_bytes(context.user_id)
        if used + pending + extra_bytes > context.quota.disk_mb * 1024 * 1024:
            raise AccessUnavailable("User aggregate private storage quota reached")
        yield


# --- Control-plane / server storage caps -----------------------------------

def _env_mb(name: str, default_mb: int) -> int:
    import os as _os

    try:
        return max(1, int(_os.environ.get(name, str(default_mb))))
    except ValueError:
        return default_mb


def control_plane_usage(*, home_root=None) -> dict:
    """Server-wide control-plane growth: database file + attachment spool."""
    from app.core.config import DB_PATH, TOMO_HOME

    db_path = Path(DB_PATH if home_root is None else Path(home_root) / "state" / "tomo.db")
    attachments = Path(TOMO_HOME if home_root is None else home_root) / "attachments"
    try:
        db_bytes = db_path.stat().st_size if db_path.is_file() else 0
    except OSError:
        db_bytes = 0
    return {"db_bytes": db_bytes, "attachments_bytes": _size(attachments) if attachments.is_dir() else 0}


def check_control_plane_limits(*, home_root=None) -> dict:
    """Refuse control-plane writes when server-wide caps are reached.

    Deployment caps, not per-user fairness: ``TOMO_MAX_DB_MB`` (default 2048)
    and ``TOMO_MAX_ATTACHMENTS_MB`` (default 10240). Operators lower them for
    small hosts. Raises ``AccessUnavailable`` (HTTP 503) at the cap.
    """
    usage = control_plane_usage(home_root=home_root)
    if usage["db_bytes"] > _env_mb("TOMO_MAX_DB_MB", 2048) * 1024 * 1024:
        raise AccessUnavailable("Server database storage capacity reached")
    if usage["attachments_bytes"] > _env_mb("TOMO_MAX_ATTACHMENTS_MB", 10240) * 1024 * 1024:
        raise AccessUnavailable("Server attachment storage capacity reached")
    return usage


# --- Quota-backed persistent storage detection ------------------------------

BOUND_MARKER = ".tomo-bounded"


def managed_storage_root() -> Path:
    """Persistent managed-storage root (personal/project workplaces live here)."""
    from app.core.config import DB_PATH
    from app.services import store as _store

    base = _store._path if getattr(_store, "_path", None) else DB_PATH
    return Path(base).absolute().parent / "managed-storage"


def managed_storage_status(*, root: Path | None = None) -> dict:
    """Capability detection for quota-backed persistent storage.

    ``bounded`` is True only when the operator provisioned a bounded volume
    (``provision-storage.sh`` writes the ``.tomo-bounded`` marker recording its
    capacity). A large ordinary filesystem reports ``bounded: False`` even
    when mostly empty: free space is not a quota. Restricted container
    admission independently refuses backing filesystems whose whole capacity
    exceeds the user's quota (see ``ContainerBackend._ensure``); this status
    is the startup/operator signal for the same requirement.
    """
    import os as _os

    target = Path(root) if root is not None else managed_storage_root()
    marker = target / BOUND_MARKER
    info: dict = {"root": str(target), "exists": target.is_dir(), "bounded": False,
                  "total_mb": 0, "available_mb": 0, "marker": None, "is_mountpoint": False}
    if target.is_dir():
        try:
            fs = _os.statvfs(target)
            info["total_mb"] = fs.f_blocks * fs.f_frsize // (1024 * 1024)
            info["available_mb"] = fs.f_bavail * fs.f_frsize // (1024 * 1024)
            info["is_mountpoint"] = _os.path.ismount(target)
        except OSError:
            pass
        try:
            if marker.is_file() and not marker.is_symlink():
                info["marker"] = marker.read_text(encoding="utf-8")[:200]
                info["bounded"] = True
        except OSError:
            pass
    return info


if __name__ == "__main__":  # Operator CLI: python -m app.runtime.storage --check
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Quota-backed managed-storage capability check")
    parser.add_argument("--check", action="store_true", help="Print managed-storage status as JSON")
    parser.add_argument("--root", default=None, help="Managed-storage root to inspect")
    parser.add_argument("--provision", action="store_true",
                        help="Print provisioning instructions (never provisions from here)")
    args = parser.parse_args()
    if args.provision:
        print("Run as host root (operator provisioning only):\n"
              "  sh deployment/sandbox/provision-storage.sh /absolute/managed-storage /absolute/storage.img APP_UID\n"
              "Then add the printed loop,nosuid,nodev fstab entry so the bound survives reboot.\n"
              "This CLI never mounts, formats, or changes host storage.")
    if args.check or not args.provision:
        status = managed_storage_status(root=Path(args.root) if args.root else None)
        print(_json.dumps(status, indent=2))
        raise SystemExit(0 if status["bounded"] else 2)

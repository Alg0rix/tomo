"""Session and permanent approval allowlists."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_session_approved: dict[str, set[str]] = {}
_permanent: set[str] | None = None


def _allowlist_path() -> Path:
    from app.core import config

    return Path(config.TOMO_HOME) / "approvals_allowlist.json"


def _load_permanent() -> set[str]:
    global _permanent
    if _permanent is not None:
        return _permanent
    path = _allowlist_path()
    keys: set[str] = set()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                keys = {str(x) for x in data if isinstance(x, str)}
            elif isinstance(data, dict) and isinstance(data.get("keys"), list):
                keys = {str(x) for x in data["keys"] if isinstance(x, str)}
        except (OSError, UnicodeError, json.JSONDecodeError):
            keys = set()
    _permanent = keys
    return _permanent


def save_permanent_allowlist() -> None:
    path = _allowlist_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"keys": sorted(_load_permanent())}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def is_approved(session_id: str | None, key: str) -> bool:
    if not key:
        return False
    if key in _load_permanent():
        return True
    if not session_id:
        return False
    with _lock:
        return key in _session_approved.get(session_id, set())


def all_keys_approved(session_id: str | None, keys: list[str]) -> bool:
    if not keys:
        return True
    return all(is_approved(session_id, k) for k in keys)


def approve_session(session_id: str, key: str) -> None:
    if not session_id or not key:
        return
    with _lock:
        _session_approved.setdefault(session_id, set()).add(key)


def approve_permanent(key: str) -> None:
    if not key:
        return
    keys = _load_permanent()
    keys.add(key)
    save_permanent_allowlist()


def clear_session_allowlist(session_id: str | None = None) -> None:
    with _lock:
        if session_id is None:
            _session_approved.clear()
        else:
            _session_approved.pop(session_id, None)


def reset_permanent_cache() -> None:
    """Test helper — drop in-memory permanent cache."""
    global _permanent
    _permanent = None


__all__ = [
    "is_approved",
    "all_keys_approved",
    "approve_session",
    "approve_permanent",
    "save_permanent_allowlist",
    "clear_session_allowlist",
    "reset_permanent_cache",
]

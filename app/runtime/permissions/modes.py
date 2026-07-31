"""Approval mode resolution and session overrides (/auto)."""

from __future__ import annotations

import threading
from typing import Literal

ApprovalMode = Literal["manual", "smart", "off"]

_lock = threading.Lock()
# session_id -> mode override (None means use settings default)
_session_modes: dict[str, ApprovalMode] = {}
# When /auto turns off, restore this if set
_session_prev: dict[str, ApprovalMode] = {}


def _settings_mode() -> ApprovalMode:
    try:
        from app.services import store

        raw = store.get_settings().get("approvals_mode", "smart")
    except Exception:
        raw = "smart"
    text = str(raw or "smart").strip().lower()
    if text in {"manual", "smart", "off"}:
        return text  # type: ignore[return-value]
    if text in {"auto", "yolo"}:
        return "off"
    return "smart"


def get_effective_mode(session_id: str | None) -> ApprovalMode:
    if session_id:
        with _lock:
            override = _session_modes.get(session_id)
        if override is not None:
            return override
    return _settings_mode()


def set_session_mode(session_id: str, mode: ApprovalMode | None) -> None:
    with _lock:
        if mode is None:
            _session_modes.pop(session_id, None)
            _session_prev.pop(session_id, None)
        else:
            _session_modes[session_id] = mode


def toggle_auto(session_id: str) -> tuple[bool, str]:
    """Toggle AUTO (mode off). Returns (auto_on, notice_text)."""
    with _lock:
        current = _session_modes.get(session_id)
        if current is None:
            current = _settings_mode()
        if current == "off":
            restore = _session_prev.pop(session_id, None) or "smart"
            if restore == "off":
                restore = "smart"
            _session_modes[session_id] = restore
            return False, f"AUTO off — using {restore}."
        _session_prev[session_id] = current
        _session_modes[session_id] = "off"
        return (
            True,
            "AUTO on — approvals bypassed (hardline still blocks).",
        )


def clear_session_modes() -> None:
    """Test helper."""
    with _lock:
        _session_modes.clear()
        _session_prev.clear()


def mode_badge(session_id: str | None) -> str:
    mode = get_effective_mode(session_id)
    return "AUTO" if mode == "off" else mode


__all__ = [
    "ApprovalMode",
    "get_effective_mode",
    "set_session_mode",
    "toggle_auto",
    "clear_session_modes",
    "mode_badge",
]

"""Approval mode resolution and session overrides (/auto)."""

from __future__ import annotations

import threading
from typing import Any, Literal

# Canonical storage: manual | smart | off  (off = Auto / unattended)
ApprovalMode = Literal["manual", "smart", "off"]

# UI shows Manual / Smart / Auto — never "off".
_LABEL: dict[ApprovalMode, str] = {
    "manual": "Manual",
    "smart": "Smart",
    "off": "Auto",
}

_lock = threading.Lock()
# session_id -> mode override (None means use settings default)
_session_modes: dict[str, ApprovalMode] = {}
# When /auto turns off, restore this if set
_session_prev: dict[str, ApprovalMode] = {}


def normalize_mode(raw: Any) -> ApprovalMode:
    """Map settings / UI / slash aliases to a canonical ApprovalMode."""
    text = str(raw or "smart").strip().lower()
    if text in {"manual", "smart", "off"}:
        return text  # type: ignore[return-value]
    # UI + slash: Auto means unattended (stored as off)
    if text in {"auto", "yolo"}:
        return "off"
    return "smart"


def _settings_mode() -> ApprovalMode:
    try:
        from app.services import store

        raw = store.get_settings().get("approvals_mode", "smart")
    except Exception:
        raw = "smart"
    return normalize_mode(raw)


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
            _session_modes[session_id] = normalize_mode(mode)


def apply_session_mode(
    session_id: str, mode: ApprovalMode | str
) -> dict[str, Any]:
    """Set session mode; if Auto (off), unstick any in-flight HITL waiters.

    Safe mid-turn: next tools see the new mode via :func:`get_effective_mode`,
    and a pending approval card is resolved as ``once`` when switching to Auto.
    """
    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("session_id required")
    target = normalize_mode(mode)
    with _lock:
        prev = _session_modes.get(sid)
        if prev is None:
            prev = _settings_mode()
        if target == "off" and prev != "off":
            _session_prev[sid] = prev
        _session_modes[sid] = target

    woken = 0
    if target == "off":
        try:
            from app.runtime.permissions.hitl import cancel_session_pending

            woken = cancel_session_pending(sid, choice="once")
        except Exception:
            woken = 0

    payload = mode_payload(sid)
    payload["cleared_pending"] = woken
    return payload


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
            return False, f"AUTO off — using {_LABEL.get(restore, restore)}."

    # Turning Auto on — share unstick path with composer / PUT override.
    result = apply_session_mode(session_id, "off")
    woken = int(result.get("cleared_pending") or 0)
    extra = f" Cleared {woken} pending approval(s)." if woken else ""
    return (
        True,
        "AUTO on — approvals bypassed (hardline still blocks)." + extra,
    )


def clear_session_modes() -> None:
    """Test helper."""
    with _lock:
        _session_modes.clear()
        _session_prev.clear()


def mode_badge(session_id: str | None) -> str:
    """Short label for UI: Manual | Smart | Auto."""
    return _LABEL[get_effective_mode(session_id)]


def mode_payload(session_id: str | None) -> dict[str, Any]:
    mode = get_effective_mode(session_id)
    label = _LABEL[mode]
    return {
        "mode": mode,  # storage: manual | smart | off
        "label": label,  # display: Manual | Smart | Auto
        "badge": f"{label} Mode",
    }


__all__ = [
    "ApprovalMode",
    "normalize_mode",
    "get_effective_mode",
    "set_session_mode",
    "apply_session_mode",
    "toggle_auto",
    "clear_session_modes",
    "mode_badge",
    "mode_payload",
]

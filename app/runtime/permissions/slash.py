"""Builtin approval-mode slash commands: /auto /smart /manual."""

from __future__ import annotations

import re

from app.runtime.permissions.modes import (
    set_session_mode,
    toggle_auto,
)

_SLASH_RE = re.compile(r"^/(auto|smart|manual)\s*$", re.IGNORECASE)

BUILTIN_SLASH = (
    {"id": "auto", "name": "auto", "description": "Toggle AUTO — run tools without approval prompts"},
    {"id": "smart", "name": "smart", "description": "Smart approvals — aux LLM assesses risky tools"},
    {"id": "manual", "name": "manual", "description": "Manual approvals — always ask for risky tools"},
)


def handle_approval_slash(message: str, session_id: str) -> str | None:
    """If ``message`` is an approval slash, apply it and return notice text."""
    text = (message or "").strip()
    m = _SLASH_RE.match(text)
    if not m or not session_id:
        return None
    cmd = m.group(1).lower()
    if cmd == "auto":
        _on, notice = toggle_auto(session_id)
        return notice
    set_session_mode(session_id, cmd)  # type: ignore[arg-type]
    return f"Approval mode set to {cmd}."


__all__ = ["handle_approval_slash", "BUILTIN_SLASH"]

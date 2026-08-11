"""Browser action risk checks and privileged-URL guards."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.runtime.browser.protocol import (
    BLOCKED_URL_PREFIXES,
    ERR_BLOCKED_ORIGIN,
    ERR_CONFIRMATION_REQUIRED,
    error_result,
)
from app.runtime.browser.tools_meta import risk_for

# Labels that often mean irreversible / monetary actions (design §23).
_SENSITIVE_LABEL = re.compile(
    r"\b("
    r"delete|remove|destroy|purchase|buy|pay|transfer|send|submit|"
    r"confirm\s+payment|checkout|unsubscribe|drop|wipe|terminate|"
    r"permanently|irreversible"
    r")\b",
    re.I,
)


def is_blocked_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    return any(u.startswith(p) for p in BLOCKED_URL_PREFIXES)


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def check_navigate_url(url: str) -> dict[str, Any] | None:
    """Return an error_result dict if navigation target is forbidden."""
    if is_blocked_url(url):
        return error_result(
            ERR_BLOCKED_ORIGIN,
            f"Navigation to privileged URL is blocked: {url}",
            recoverable=False,
        )
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return error_result(
            ERR_BLOCKED_ORIGIN,
            f"Only http/https navigation is allowed (got {parsed.scheme or 'empty'})",
            recoverable=False,
        )
    return None


def maybe_confirmation_required(
    tool: str,
    arguments: dict[str, Any],
    *,
    target_name: str = "",
) -> dict[str, Any] | None:
    """Heuristic sensitive-action gate (independent of the LLM).

    Returns an error_result when the action should pause for user approval.
    V1 surfaces CONFIRMATION_REQUIRED to the agent; full HITL resume is
    follow-up work (frontend can still approve via extension popup later).
    """
    risk = risk_for(tool)
    label = (target_name or "").strip()
    if not label:
        # Click/type may pass name via ref metadata later.
        label = str(arguments.get("name") or arguments.get("label") or "")
    if risk in {"sensitive", "destructive"}:
        return error_result(
            ERR_CONFIRMATION_REQUIRED,
            f"Sensitive browser action '{tool}' requires confirmation.",
            recoverable=True,
            extra={
                "action": {"type": tool, "target": label or tool},
            },
        )
    if tool == "browser_click" and label and _SENSITIVE_LABEL.search(label):
        return error_result(
            ERR_CONFIRMATION_REQUIRED,
            f"Click on sensitive control requires confirmation: {label!r}",
            recoverable=True,
            extra={
                "action": {"type": "browser.click", "target": label},
            },
        )
    return None

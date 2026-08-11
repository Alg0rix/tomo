"""Versioned message envelope for Tomo Browser Control (tomo.browser.v1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

PROTOCOL = "tomo.browser.v1"

# Frontend ↔ backend (WebSocket)
TYPE_HELLO = "browser.hello"
TYPE_CAPABILITIES = "browser.capabilities"
TYPE_TABS_UPDATED = "browser.tabs.updated"
TYPE_TOOL_EXECUTE = "browser.tool.execute"
TYPE_TOOL_RESULT = "browser.tool.result"
TYPE_TOOL_ERROR = "browser.tool.error"
TYPE_TOOL_CANCEL = "browser.tool.cancel"
TYPE_SESSION_CLOSED = "browser.session.closed"
TYPE_HEARTBEAT = "browser.heartbeat"
TYPE_PERMISSION_REQUEST = "browser.permission.request"
TYPE_PERMISSION_RESULT = "browser.permission.result"

# Extension discovery (page → extension)
TYPE_PING = "TOMO_PING"
TYPE_PONG = "TOMO_PONG"

# Default capability set advertised by the extension (V1 MVP).
DEFAULT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "browser.tabs",
        "browser.attach",
        "browser.snapshot",
        "browser.click",
        "browser.type",
        "browser.press",
        "browser.select",
        "browser.scroll",
        "browser.navigate",
        "browser.back",
        "browser.forward",
        "browser.wait",
        "browser.screenshot",
        "browser.extract",
    }
)

# Privileged Chrome URL schemes the driver must refuse.
BLOCKED_URL_PREFIXES: tuple[str, ...] = (
    "chrome://",
    "chrome-extension://",
    "devtools://",
    "edge://",
    "about:",
    "file://",
)


def new_id(prefix: str = "evt") -> str:
    """Short prefixed id (``brs_…``, ``call_…``, ``tab_…``)."""
    return f"{prefix}_{uuid4().hex[:16]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def envelope(
    msg_type: str,
    *,
    session_id: str = "",
    payload: dict[str, Any] | None = None,
    msg_id: str | None = None,
) -> dict[str, Any]:
    """Build a versioned protocol envelope."""
    return {
        "protocol": PROTOCOL,
        "type": msg_type,
        "id": msg_id or new_id("evt"),
        "session_id": session_id or "",
        "timestamp": utc_now_iso(),
        "payload": payload if isinstance(payload, dict) else {},
    }


def error_result(
    code: str,
    message: str,
    *,
    recoverable: bool = False,
    suggested_action: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standard tool error payload returned to the agent."""
    err: dict[str, Any] = {
        "code": code,
        "message": message,
        "recoverable": recoverable,
    }
    if suggested_action:
        err["suggested_action"] = suggested_action
    if extra:
        err.update(extra)
    return {"success": False, "error": err}


def ok_result(**fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"success": True}
    out.update(fields)
    return out


# Error codes (design §26)
ERR_EXTENSION_NOT_AVAILABLE = "EXTENSION_NOT_AVAILABLE"
ERR_BROWSER_DISCONNECTED = "BROWSER_DISCONNECTED"
ERR_TAB_NOT_FOUND = "TAB_NOT_FOUND"
ERR_TAB_NOT_AUTHORIZED = "TAB_NOT_AUTHORIZED"
ERR_ATTACH_FAILED = "ATTACH_FAILED"
ERR_STALE_ELEMENT = "STALE_ELEMENT"
ERR_ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
ERR_ELEMENT_NOT_INTERACTABLE = "ELEMENT_NOT_INTERACTABLE"
ERR_NAVIGATION_FAILED = "NAVIGATION_FAILED"
ERR_TIMEOUT = "TIMEOUT"
ERR_PERMISSION_DENIED = "PERMISSION_DENIED"
ERR_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
ERR_CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"
ERR_SESSION_EXPIRED = "SESSION_EXPIRED"
ERR_BLOCKED_ORIGIN = "BLOCKED_ORIGIN"

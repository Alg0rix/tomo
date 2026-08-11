"""Lightweight browser tool audit log (redacted).

Does not store typed passwords, cookies, HTML, or input values.
Best-effort SQLite append; failures are swallowed so tool execution continues.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.runtime.browser.protocol import new_id

logger = logging.getLogger(__name__)


def _redact_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep structure/metadata only — never raw typed text."""
    safe: dict[str, Any] = {"tool": tool}
    for key in ("tab_id", "ref", "snapshot_version", "url", "direction", "key"):
        if key in arguments:
            safe[key] = arguments[key]
    if "text" in arguments:
        text = arguments.get("text")
        length = len(text) if isinstance(text, str) else 0
        # Heuristic: password-ish fields often short opaque; always redact body.
        safe["input"] = {"redacted": True, "length": length}
    if "submit" in arguments:
        safe["submit"] = bool(arguments.get("submit"))
    return safe


def record_execution(
    *,
    user_id: str = "",
    conversation_id: str = "",
    agent_id: str = "",
    browser_session_id: str = "",
    tool: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    duration_ms: float = 0.0,
    status: str = "succeeded",
    error_code: str = "",
) -> None:
    """Append one audit row (best-effort)."""
    args = arguments if isinstance(arguments, dict) else {}
    meta = _redact_arguments(tool, args)
    tab_id = str(args.get("tab_id") or "")
    domain = ""
    if isinstance(result, dict):
        page = result.get("page") or result.get("tab") or {}
        if isinstance(page, dict):
            url = str(page.get("url") or "")
            if "://" in url:
                try:
                    domain = url.split("://", 1)[1].split("/", 1)[0]
                except Exception:
                    domain = ""
    try:
        from app.services import store

        store.append_browser_audit(
            {
                "id": new_id("baud"),
                "user_id": user_id or "",
                "conversation_id": conversation_id or "",
                "agent_id": agent_id or "",
                "browser_session_id": browser_session_id or "",
                "tab_id": tab_id,
                "domain": domain,
                "tool": tool,
                "arguments_meta": meta,
                "status": status,
                "error_code": error_code or "",
                "duration_ms": float(duration_ms),
                "created_at": time.time(),
            }
        )
    except Exception:
        logger.debug("browser audit write failed", exc_info=True)


def format_audit_json(row: dict[str, Any]) -> str:
    return json.dumps(row, separators=(",", ":"), default=str)

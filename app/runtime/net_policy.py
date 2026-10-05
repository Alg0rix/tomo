"""Scoped outbound-network egress policy for agent tools.

Restricted containers run with ``network=none``; these tools run in the
coordinator process instead, so they need their own owned gate. Outbound
networking defaults to **off** (fail closed). An operator may enable
``scoped`` egress, which permits public HTTP(S) retrieval only — the
per-tool SSRF/private-host guards still apply on every request, and every
admission is audited through the normal tool path.

This is a capability switch, not a sandbox: ``scoped`` traffic originates
from the server host (shared egress IP, no per-user network namespace).
Only enable it on hosts where that is acceptable; Members inherit the same
host-level route, attributed to their own identity in the audit log.

Operator provisioning: ``PUT /api/settings {"network_egress": "scoped"}``
(System → Settings, Admin only). Unknown values are rejected, never
coerced — an unsafe or misspelled config denies rather than opens.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SETTING_KEY = "network_egress"
OFF = "off"
SCOPED = "scoped"
MODES = (OFF, SCOPED)


def egress_mode() -> str:
    """Current egress mode from settings; unknown/missing reads as ``off``."""
    try:
        from app.services import store

        raw = store.get_settings().get(SETTING_KEY, OFF)
    except Exception:
        return OFF
    mode = str(raw or OFF).strip().lower()
    if mode not in MODES:
        logger.warning("network egress misconfigured (%r); denying outbound network", raw)
        return OFF
    return mode


def validate_egress_value(value) -> str:
    """Normalize a candidate setting; raise ValueError on unsafe input."""
    mode = str(value or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"{SETTING_KEY} must be one of {list(MODES)}")
    return mode


def require_egress(tool_name: str) -> str:
    """Admit *tool_name* for outbound network, or raise fail-closed.

    Must be called after :func:`app.runtime.policy.authorize_tool` so the
    owned execution ceiling (and its audit row) already exists.
    """
    from app.runtime.access import AccessUnavailable

    mode = egress_mode()
    if mode != SCOPED:
        raise AccessUnavailable(
            f"Outbound network is disabled (network_egress={mode}); "
            "ask an Admin to enable scoped egress"
        )
    return mode


__all__ = ["SETTING_KEY", "OFF", "SCOPED", "MODES", "egress_mode", "validate_egress_value", "require_egress"]

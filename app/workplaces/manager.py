"""Workplace lifecycle — Connect/test dispatcher (Alpha Slice D)."""

from __future__ import annotations

from typing import Any

from app.workplaces.backends import local, ssh, tunnel

_BACKENDS = {
    "local": local.test_connection,
    "ssh": ssh.test_connection,
    "tunnel": tunnel.test_connection,
}


def connect(workplace: dict[str, Any]) -> dict[str, Any]:
    """Run the kind-specific Connect probe.

    Returns ``{"ok": bool, "status": str, "message": str}``. Tunnel never
    becomes ``connected``. Local/SSH set ``connected`` on success, ``offline``
    on failure.
    """
    kind = (workplace.get("kind") or "").strip().lower()
    tester = _BACKENDS.get(kind)
    if tester is None:
        return {
            "ok": False,
            "status": "offline",
            "message": f"Unknown workplace kind: {kind or '(empty)'}",
        }
    ok, message = tester(workplace)
    if kind == "tunnel":
        status = "later"
        ok = False
    else:
        status = "connected" if ok else "offline"
    return {"ok": ok, "status": status, "message": message}


__all__ = ["connect"]

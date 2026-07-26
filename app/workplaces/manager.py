"""Workplace lifecycle — Connect/test dispatcher."""

from __future__ import annotations

from typing import Any

from app.workplaces.backends import local, ssh, tunnel
from app.workplaces.hub import hub

_BACKENDS = {
    "local": local.test_connection,
    "ssh": ssh.test_connection,
    "tunnel": tunnel.test_connection,
}


def connect(workplace: dict[str, Any]) -> dict[str, Any]:
    """Run the kind-specific Connect probe.

    Returns ``{"ok": bool, "status": str, "message": str}``.
    Tunnel is ``connected`` only when the hub has a live WebSocket.
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
        wid = (workplace.get("id") or "").strip()
        if ok and hub.is_online(wid):
            status = "connected"
        elif workplace.get("pairing_code") or (workplace.get("status") == "pairing"):
            status = "pairing"
            ok = False
        else:
            status = "offline"
            ok = False
    else:
        status = "connected" if ok else "offline"
    return {"ok": ok, "status": status, "message": message}


__all__ = ["connect"]

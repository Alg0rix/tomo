"""WebSocket tunnel workplace (Tomo Connector).

Connect reports real online status from the in-process hub — never fake
``connected`` without a live socket.
"""

from __future__ import annotations

from typing import Any

from app.workplaces.hub import hub


def test_connection(workplace: dict[str, Any]) -> tuple[bool, str]:
    """Return ok if a connector session is registered for this workplace."""
    wid = (workplace.get("id") or "").strip()
    if not wid:
        return False, "Missing workplace id"
    if hub.is_online(wid):
        session = hub.get(wid)
        host = (session.hostname if session else "") or workplace.get("host") or wid
        return True, f"Connector online ({host})"
    status = (workplace.get("status") or "").strip().lower()
    if status == "pairing" or workplace.get("pairing_code"):
        return False, "Waiting for connector — run tomo-connector pair with the code"
    if workplace.get("connector_token_set"):
        return False, "Connector offline — run tomo-connector run to reconnect"
    return False, "No connector paired yet — generate a pairing code"


__all__ = ["test_connection"]

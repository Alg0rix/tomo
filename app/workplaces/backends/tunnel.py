"""WebSocket tunnel workplace (Tomo Connector) — Alpha stub.

Tunnel workplaces are allowed in the schema so the UI can create the type,
but Connect never reports success. Status stays ``later`` with an honest
“connector later” label — not fake-connected.
"""

from __future__ import annotations

from typing import Any


def test_connection(workplace: dict[str, Any]) -> tuple[bool, str]:
    """Always fail honestly — Connector product is post-Alpha."""
    _ = workplace
    return False, "Tomo Connector later — tunnel workplaces are not connectable yet"


__all__ = ["test_connection"]

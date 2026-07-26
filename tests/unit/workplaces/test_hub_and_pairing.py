"""Connector hub, pairing TTL, and offline tool routing (no real network)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services import store
from app.workplaces import pairing as pairing_mod
from app.workplaces.hub import ConnectorSession, hub
from app.workplaces.pairing import generate_pairing_code, pairing_expires_at


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "connector.db")
    hub.reset()
    pairing_mod.rate_limiter.reset()


def test_generate_pairing_code_shape() -> None:
    code = generate_pairing_code()
    assert len(code) == 6
    assert code.isalnum()
    assert code == code.upper() or any(c.isdigit() for c in code)


def test_pairing_expires_at_in_future() -> None:
    now = time.time()
    exp = pairing_expires_at(now)
    assert exp > now
    assert exp - now >= 60


def test_hub_offline_call_error() -> None:
    hub.reset()
    result = hub.call("missing", "ping", {})
    assert result["ok"] is False
    assert "offline" in result["error"].lower()


def test_hub_rpc_round_trip(tmp_path: Path) -> None:
    """Mock websocket + event loop: register session, resolve RPC manually."""
    hub.reset()
    loop = asyncio.new_event_loop()
    ws = MagicMock()

    async def _send(msg: dict[str, Any]) -> None:
        # Immediately resolve as if the connector replied.
        rid = msg.get("id")
        if rid and session:
            session.resolve_rpc(
                rid, {"ok": True, "result": f"pong:{msg.get('method')}"}
            )

    ws.send_json = lambda msg: asyncio.ensure_future(_send(msg), loop=loop)
    # ConnectorSession.send awaits websocket.send_json — use async mock.
    async def send_json(msg: dict[str, Any]) -> None:
        await _send(msg)

    ws.send_json = send_json

    session = ConnectorSession("wp_x", ws, loop, hostname="test-host")
    hub.register(session)
    assert hub.is_online("wp_x")

    async def _run() -> dict[str, Any]:
        # Drive pending callbacks while call waits on another thread.
        fut = asyncio.get_event_loop().run_in_executor(
            None, lambda: hub.call("wp_x", "bash", {"command": "true"}, timeout=5.0)
        )
        # Pump loop a bit.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if fut.done():
                break
        return await fut

    try:
        result = loop.run_until_complete(_run())
    finally:
        hub.reset()
        loop.close()

    assert result["ok"] is True
    assert "bash" in str(result["result"])


def test_pair_invalid_code(tmp_path: Path) -> None:
    _rebind(tmp_path)
    assert store.pair_connector("NOPE12") is None


def test_pair_expired_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    code = wp["pairing_code"]
    # Force expiry in DB.
    store._conn.execute(
        "UPDATE workplaces SET pairing_expires_at=? WHERE id=?",
        (time.time() - 10, "wp_tun"),
    )
    store._conn.commit()
    assert store.pair_connector(code) is None


def test_status_cannot_force_tunnel_connected_via_api(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    store.update_workplace("wp_tun", {"status": "connected"})
    assert store.get_workplace("wp_tun")["status"] != "connected"


def test_pairing_code_avoids_ambiguous_chars() -> None:
    for _ in range(40):
        code = generate_pairing_code()
        assert not any(c in code for c in "01OI")


def test_client_supports_replay() -> None:
    from app.workplaces.hub import client_supports_replay

    assert client_supports_replay(caps="idempotent-replay") is True
    assert client_supports_replay(version="0.2.0") is True
    assert client_supports_replay(version="0.1.0") is False

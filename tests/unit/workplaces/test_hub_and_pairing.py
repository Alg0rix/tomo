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






def test_hub_offline_call_error() -> None:
    hub.reset()
    result = hub.call("missing", "ping", {})
    assert result["ok"] is False
    assert "offline" in result["error"].lower()




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


def test_disable_tunnel_revokes_pairing_and_blocks_connector(tmp_path: Path) -> None:
    """Disable clears pairing + flips status; connector auth is refused."""
    _rebind(tmp_path)
    wp = store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    code = wp["pairing_code"]
    assert store.get_workplace("wp_tun")["enabled"] is True

    got = store.set_workplace_enabled("wp_tun", False)
    assert got is not None
    assert got["enabled"] is False
    assert got["status"] == "disabled"
    assert got["pairing_code"] == ""

    # Pairing refused for revoked code.
    assert store.pair_connector(code) is None
    # Connector hello refused while disabled.
    assert store.hello_connector("whatever-token") is None

    # Re-enable -> offline, connector token kept enables reconnect.
    got = store.set_workplace_enabled("wp_tun", True)
    assert got["enabled"] is True
    assert got["status"] == "offline"


def test_disable_local_rejected(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_loc",
            "name": "L",
            "kind": "local",
            "root_path": str(tmp_path),
        }
    )
    with pytest.raises(ValueError):
        store.set_workplace_enabled("wp_loc", False)
    assert store.get_workplace("wp_loc")["enabled"] is True


def test_set_workplace_enabled_unknown_returns_none(tmp_path: Path) -> None:
    _rebind(tmp_path)
    assert store.set_workplace_enabled("nope", False) is None






@pytest.mark.asyncio
async def test_replay_after_real_disconnect_resolves_original_caller() -> None:
    from app.api.connector import _bind_session

    # Exercise _bind_session with its real global hub; leave store untouched.
    hub.reset()
    loop = asyncio.get_running_loop()
    sent: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    old_ws = MagicMock()

    async def old_send(msg: dict[str, Any]) -> None:
        await sent.put(msg)

    async def close(**_: Any) -> None:
        pass

    old_ws.send_json = old_send
    old_ws.close = close
    old = ConnectorSession("reconnect-test", old_ws, loop, replay_ok=True)
    hub.register(old)
    caller = asyncio.create_task(asyncio.to_thread(old.call, "write_file", {"content": "once"}, timeout=2))
    try:
        original = await asyncio.wait_for(sent.get(), 2)
        assert hub.unregister(old.workplace_id, old_ws)
        assert not hub.is_online(old.workplace_id)
        replayed: list[dict[str, Any]] = []
        new_ws = MagicMock()

        async def send_text(raw: str) -> None:
            import json

            replayed.append(json.loads(raw))

        new_ws.send_text = send_text
        new_ws.close = close
        new = await asyncio.wait_for(
            _bind_session(new_ws, old.workplace_id, hostname="host", version="0.2.0"),
            timeout=1,
        )
        assert replayed == [original]
        new.resolve_rpc(original["id"], {"ok": True, "result": "saved"})
        assert await asyncio.wait_for(caller, 2) == {"ok": True, "result": "saved"}
        assert not new._pending
        assert not hub.unregister(old.workplace_id, old_ws)
        assert hub.get(old.workplace_id) is new
    finally:
        hub.reset()
        await asyncio.wait_for(caller, 2)


@pytest.mark.asyncio
async def test_replayed_timeout_cleans_adopted_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workplaces import hub as hub_module

    monkeypatch.setattr(hub_module, "DISCONNECT_GRACE", 0.02)
    loop = asyncio.get_running_loop()
    ws = MagicMock()
    sent = asyncio.Event()

    async def send_json(_: dict[str, Any]) -> None:
        sent.set()

    async def send_text(_: str) -> None:
        pass

    ws.send_json = send_json
    ws.send_text = send_text
    old = ConnectorSession("timeout", ws, loop, replay_ok=True)
    new = ConnectorSession("timeout", ws, loop, replay_ok=True)
    caller = asyncio.create_task(asyncio.to_thread(old.call, "ping", timeout=0.1))
    await asyncio.wait_for(sent.wait(), 1)
    await new.adopt_pending(old.take_pending_for_replay())
    result = await asyncio.wait_for(caller, 1)
    assert not result["ok"]
    assert "timed out" in result["error"]
    assert not new._pending
    assert not new._pending_msg


@pytest.mark.asyncio
async def test_disconnect_grace_expires_and_legacy_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import concurrent.futures
    from app.workplaces import hub as hub_module
    from app.workplaces.hub import ConnectorHub

    monkeypatch.setattr(hub_module, "DISCONNECT_GRACE", 0.02)
    loop = asyncio.get_running_loop()
    local_hub = ConnectorHub()
    for replay_ok in (True, False):
        session = ConnectorSession("grace", MagicMock(), loop, replay_ok=replay_ok)
        pending: concurrent.futures.Future = concurrent.futures.Future()
        session._pending["request"] = pending
        session._pending_msg["request"] = "{}"
        local_hub.register(session)
        assert local_hub.unregister("grace", session.websocket)
        assert not local_hub.is_online("grace")
        if replay_ok:
            assert not pending.done()
            await asyncio.sleep(0.1)
        assert pending.done()
        assert not pending.result()["ok"]
        assert not local_hub._disconnected
    local_hub.reset()


def test_pending_rpc_capacity() -> None:
    from app.workplaces.hub import MAX_PENDING_RPCS
    import concurrent.futures

    loop = asyncio.new_event_loop()
    try:
        session = ConnectorSession("capacity", MagicMock(), loop)
        for i in range(MAX_PENDING_RPCS):
            session._pending[str(i)] = concurrent.futures.Future()
        result = session.call("ping", timeout=0.01)
        assert not result["ok"]
        assert "busy" in result["error"]
        assert len(session._pending) == MAX_PENDING_RPCS
        session.fail_all("cleanup")
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_adoption_respects_pending_capacity() -> None:
    import concurrent.futures
    from unittest.mock import AsyncMock
    from app.workplaces.hub import MAX_PENDING_RPCS

    ws = MagicMock()
    ws.send_text = AsyncMock()
    session = ConnectorSession("adoption-capacity", ws, asyncio.get_running_loop())
    for i in range(MAX_PENDING_RPCS):
        session._pending[str(i)] = concurrent.futures.Future()
    extra: concurrent.futures.Future = concurrent.futures.Future()
    await session.adopt_pending([("extra", "{}", extra)])
    assert len(session._pending) == MAX_PENDING_RPCS
    assert extra.done()
    assert "uncertain" in extra.result()["error"]
    ws.send_text.assert_not_awaited()
    session.fail_all("cleanup")

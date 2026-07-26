"""Tunnel tool routing: offline error + mock hub RPC for bash/files."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.runtime.tools import bash, read_file, sandbox, write_file
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from app.workplaces.hub import hub


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path) -> None:
    store.rebind(tmp_path / "tunnel-tools.db")
    hub.reset()
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    hub.reset()
    reset_registry()


def _tunnel_agent() -> None:
    store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    # Pair so token exists (status connected in DB is ok for offline tool test).
    code = store.get_workplace("wp_tun")["pairing_code"]
    store.pair_connector(code, hostname="dev")
    store.update_agent("ops", {"workplace_id": "wp_tun"})
    sandbox.bind_agent("ops")


def test_tunnel_offline_bash_error() -> None:
    _tunnel_agent()
    result = bash.run({"command": "pwd"})
    assert result.startswith("Error:")
    assert "offline" in result.lower()


def test_tunnel_offline_read_write_error() -> None:
    _tunnel_agent()
    assert execute("read_file", {"path": "a.txt"}).startswith("Error:")
    assert execute(
        "write_file", {"path": "a.txt", "content": "x"}
    ).startswith("Error:")


def test_tunnel_rpc_bash_via_mock_hub() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        assert wid == "wp_tun"
        assert method == "bash"
        return {"ok": True, "result": "/remote/cwd\nhello-tunnel"}

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        result = bash.run({"command": "pwd && echo hello-tunnel"})
    assert "hello-tunnel" in result
    assert not result.startswith("Error:")


def test_tunnel_rpc_files_via_mock_hub() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        if method == "write_file":
            assert params["path"] == "note.txt"
            assert params["content"] == "hi"
            return {"ok": True, "result": "Wrote 2 bytes to note.txt"}
        if method == "read_file":
            return {"ok": True, "result": "hi"}
        return {"ok": False, "error": f"unexpected {method}"}

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        w = write_file.run({"path": "note.txt", "content": "hi"})
        r = read_file.run({"path": "note.txt"})
    assert "Wrote" in w
    assert r == "hi"


def test_local_workplace_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "local-root"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_local", "name": "L", "kind": "local", "root_path": str(root)}
    )
    store.update_agent("ops", {"workplace_id": "wp_local"})
    sandbox.bind_agent("ops")
    result = bash.run({"command": "pwd && echo local-ok"})
    assert "local-ok" in result
    assert str(root.resolve()) in result

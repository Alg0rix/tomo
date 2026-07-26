"""Workplace remote routing (tunnel + ssh) without real network."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.runtime.tools import sandbox
from app.runtime.tools.registry import reset_registry
from app.runtime.tools.workplace_remote import format_rpc_result, try_remote
from app.services import store
from app.workplaces.hub import hub


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "remote.db")
    hub.reset()
    reset_registry()
    sandbox.reset_agent()


def test_format_str_replace_delete() -> None:
    assert "Replaced" in format_rpc_result(
        "str_replace", {"ok": True, "path": "a.txt"}
    )
    assert "Deleted" in format_rpc_result(
        "delete_file", {"ok": True, "path": "a.txt"}
    )


def test_local_returns_none(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "r"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_l", "name": "L", "kind": "local", "root_path": str(root)}
    )
    store.update_agent("ops", {"workplace_id": "wp_l"})
    sandbox.bind_agent("ops")
    assert try_remote("exec_bash", {"script": "true"}) is None


def test_tunnel_offline_error(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace({"id": "wp_t", "name": "T", "kind": "tunnel"})
    code = store.get_workplace("wp_t")["pairing_code"]
    store.pair_connector(code)
    store.update_agent("ops", {"workplace_id": "wp_t"})
    sandbox.bind_agent("ops")
    out = try_remote("exec_bash", {"script": "true"})
    assert out is not None and out.startswith("Error:")
    assert "offline" in out.lower()


def test_all_tunnels_hint_picks_host(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {"id": "wp_aio", "name": "aio-serv", "kind": "tunnel"}
    )
    store.create_workplace(
        {"id": "wp_other", "name": "other", "kind": "tunnel"}
    )
    store.update_agent(
        "ops",
        {"workplace_scope": "all_tunnels", "workplace_id": "", "workplace_ids": []},
    )
    sandbox.bind_agent("ops")
    from app.runtime.tools.workplace_ctx import bind_workplace, reset_workplace
    from app.runtime.tools.workplace_remote import resolve_agent_workplace

    toks = bind_workplace(hint="aio-serv")
    try:
        wp = resolve_agent_workplace("ops")
        assert wp is not None
        assert wp["id"] == "wp_aio"
    finally:
        reset_workplace(toks)


def test_ssh_routes_to_ssh_exec(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_s",
            "name": "S",
            "kind": "ssh",
            "ssh_host": "h",
            "ssh_user": "u",
            "ssh_password": "p",
        }
    )
    store.update_agent("ops", {"workplace_id": "wp_s"})
    sandbox.bind_agent("ops")

    with patch(
        "app.workplaces.ssh_exec.call",
        return_value={
            "ok": True,
            "result": {
                "stdout": "remote-ssh\n",
                "stderr": "",
                "exit_code": 0,
            },
        },
    ) as mocked:
        out = try_remote("exec_bash", {"script": "echo hi"})
    assert out is not None
    assert "remote-ssh" in out
    mocked.assert_called()

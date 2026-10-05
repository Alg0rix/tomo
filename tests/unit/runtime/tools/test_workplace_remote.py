"""Workplace remote routing (tunnel + ssh) without real network."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.runtime.access import execution_scope
from app.runtime.tools import sandbox
from app.runtime.tools.registry import reset_registry
from app.runtime.tools.workplace_remote import format_rpc_result, try_remote
from app.services import store
from app.workplaces import remote_contract
from app.workplaces.hub import hub
from tests.fakes.remote import FakeDestination


def _rebind(tmp_path: Path) -> None:
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "remote.db")
    ensure_stoppers()
    hub.reset()
    remote_contract.reset()
    reset_registry()
    sandbox.reset_agent()


def _online_tunnel(tmp_path: Path, wid: str = "wp_t", name: str = "T") -> FakeDestination:
    """Paired 0.4.0 tunnel + enforcing destination (reconnects negotiate)."""
    store.create_workplace({"id": wid, "name": name, "kind": "tunnel"})
    code = store.get_workplace(wid)["pairing_code"]
    store.pair_connector(code, hostname="dev", version="0.4.0")
    dest = FakeDestination(
        wid, tmp_path / f"dest-{wid}",
        caps="idempotent-replay,exec-stream,exec-context-v1,remote-sandbox-v1",
        sandbox_image="sha256:test",
    )
    dest.register()
    return dest


def _member_chat(tmp_path: Path, user_id: str, wid: str, *, mode: str = "restricted") -> str:
    profile = store.create_llm_profile({"name": "M", "model": "m"})
    store.access.assign("usr_admin", user_id, "model", profile["id"])
    store.access.assign("usr_admin", user_id, "workplace", wid, permission="read_write")
    ack = mode == "unrestricted"
    if ack:
        store.access.assign("usr_admin", user_id, "unrestricted", wid)
    sid = store.create_swarm_session(["main"], user_id=user_id)
    store.access.set_chat_access(user_id, sid, wid, execution_mode=mode,
                                 unrestricted_acknowledged=ack)
    return sid


def test_format_str_replace_delete() -> None:
    assert "Replaced" in format_rpc_result(
        "str_replace", {"ok": True, "path": "a.txt", "replacements": 2}
    )
    assert "2" in format_rpc_result(
        "str_replace", {"ok": True, "path": "a.txt", "replacements": 2}
    )
    assert "Deleted" in format_rpc_result(
        "delete_file", {"ok": True, "path": "a.txt"}
    )
    assert "hunk" in format_rpc_result(
        "patch", {"ok": True, "path": "a.txt", "hunks_applied": 3}
    ).lower()



def test_local_returns_none(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "r"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_l", "name": "L", "kind": "local", "root_path": str(root)}
    )
    store.update_agent("ops", {"workplace_id": "wp_l"})
    sandbox.bind_agent("ops")
    from tests.fakes.access import owned_admin_scope
    with owned_admin_scope():
        assert try_remote("exec_bash", {"script": "true"}) is None


def test_tunnel_offline_error(tmp_path: Path) -> None:
    # An offline tunnel in the active chat fails closed at authorization with
    # a clear destination error; nothing executes locally or remotely.
    import pytest
    from app.runtime.access import AccessUnavailable
    _rebind(tmp_path)
    store.create_workplace({"id": "wp_t", "name": "T", "kind": "tunnel"})
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.set_chat_access("usr_admin", sid, "wp_t", execution_mode="restricted")
    with pytest.raises(AccessUnavailable, match="(?i)offline"):
        store.access.resolve_context("usr_admin", sid)


def test_all_tunnels_hint_picks_host(tmp_path: Path) -> None:
    _rebind(tmp_path)
    dest_a = _online_tunnel(tmp_path, wid="wp_aio", name="aio-serv")
    dest_b = _online_tunnel(tmp_path, wid="wp_other", name="other")
    try:
        store.update_agent(
            "ops",
            {"workplace_scope": "all_tunnels", "workplace_id": "", "workplace_ids": []},
        )
        sandbox.bind_agent("ops")
        store.access.assign("usr_admin", "usr_admin", "workplace", "wp_aio", permission="read_write")
        store.access.assign("usr_admin", "usr_admin", "workplace", "wp_other", permission="read_write")
        sid = store.create_swarm_session(["main"], user_id="usr_admin")
        store.access.set_chat_access("usr_admin", sid, "wp_aio",
                                     additional_workplace_ids=["wp_other"])
        from app.runtime.tools.workplace_ctx import bind_workplace, reset_workplace
        from app.runtime.tools.workplace_remote import resolve_agent_workplace
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            toks = bind_workplace(hint="aio-serv")
            try:
                wp = resolve_agent_workplace("ops")
                assert wp is not None
                assert wp["id"] == "wp_aio"
            finally:
                reset_workplace(toks)
    finally:
        dest_a.unregister()
        dest_b.unregister()


def test_workplace_hint_cannot_move_execution_ceiling(tmp_path: Path) -> None:
    # A per-call workplace hint never moves execution to another machine:
    # exec stays at the chat's active destination (transfer endpoints need
    # an explicit authorized transfer, not a tool argument). The denial is
    # destination-specific; nothing executes locally or remotely.
    _rebind(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    store.rebind(sub / "remote.db")
    from tests.fakes.access import ensure_stoppers as _ensure
    _ensure()
    root = tmp_path / "r"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_l", "name": "L", "kind": "local", "root_path": str(root)}
    )
    dest = _online_tunnel(tmp_path, wid="wp_aio", name="aio-serv")
    try:
        store.update_agent("ops", {"workplace_ids": ["wp_l", "wp_aio"]})
        sandbox.bind_agent("ops")
        store.access.assign("usr_admin", "usr_admin", "workplace", "wp_l", permission="read_write")
        store.access.assign("usr_admin", "usr_admin", "workplace", "wp_aio", permission="read_write")
        sid = store.create_swarm_session(["main"], user_id="usr_admin")
        store.access.set_chat_access("usr_admin", sid, "wp_l",
                                     additional_workplace_ids=["wp_aio"])
        from app.runtime.access import AccessDenied
        from app.runtime.tools.workplace_ctx import bind_workplace, reset_workplace
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            toks = bind_workplace(workplace_id="wp_l")
            try:
                with pytest.raises(AccessDenied):
                    try_remote("exec_bash", {"script": "true"}, workplace_hint="aio-serv")
            finally:
                reset_workplace(toks)
            # The active destination itself still executes (hint re-affirms).
            sid2 = store.create_swarm_session(["main"], user_id="usr_admin")
            store.access.set_chat_access("usr_admin", sid2, "wp_aio")
        with execution_scope(store.access.resolve_context("usr_admin", sid2)):
            out = try_remote("exec_bash", {"script": "echo hint-ok"}, workplace_hint="aio-serv")
            assert out is not None and "hint-ok" in out
    finally:
        dest.unregister()


def test_ssh_routes_to_ssh_exec_with_grant_and_ack(tmp_path: Path) -> None:
    # Broad-host SSH is unrestricted-only: matching Admin grant + explicit
    # chat acknowledgement, quota-capped. The transport (paramiko) is the
    # only stand-in; authorization is real.
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
    # Simulate a probed-online host (the probe needs a real SSH server;
    # the grant/ack/quota authorization under test does not).
    store.with_db(lambda c: (c.execute("UPDATE workplaces SET status='connected' WHERE id='wp_s'"), c.commit()))
    store.access.assign("usr_admin", "usr_admin", "workplace", "wp_s", permission="read_write")
    store.access.assign("usr_admin", "usr_admin", "unrestricted", "wp_s")
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.set_chat_access("usr_admin", sid, "wp_s", execution_mode="unrestricted",
                                 unrestricted_acknowledged=True)
    seen = {}

    def _fake_ssh(workplace, method, params):
        seen.update(method=method, timeout=params.get("timeout"))
        return {"ok": True, "result": {"stdout": "remote-ssh\n", "stderr": "", "exit_code": 0}}

    with patch("app.workplaces.ssh_exec.call", side_effect=_fake_ssh) as mocked:
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            out = try_remote("exec_bash", {"script": "echo hi"})
    assert out is not None
    assert "remote-ssh" in out
    mocked.assert_called()
    assert seen["method"] == "exec_bash"
    # Quota duration caps the SSH timeout (default test quota: 300s).
    assert seen["timeout"] and seen["timeout"] <= 300

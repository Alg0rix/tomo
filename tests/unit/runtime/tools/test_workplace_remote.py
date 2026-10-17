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


@pytest.mark.parametrize("via_registry", [False, True])
def test_admin_personal_chat_routes_to_tunnel_without_switch(tmp_path, via_registry):
    from app.runtime.access import current_execution
    from app.runtime.tools.registry import get_registry

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        original = store.get_session(sid)["workplace_id"]
        context = store.access.resolve_context("usr_admin", sid)
        with execution_scope(context):
            if via_registry:
                out = get_registry().execute("bash", {"command": "echo routed-ok", "workplace": "ops-host"})
            else:
                out = try_remote("exec_bash", {"script": "echo routed-ok"}, workplace_hint="ops-host")
            assert "routed-ok" in out
            assert current_execution() == context
        session = store.get_session(sid)
        assert session["workplace_id"] == original
        assert "wp_ops" in session["additional_workplace_ids"]
        # Stop/revoke must include a destination used without a UI switch.
        from app.workplaces.remote_contract import stop_session
        stop_session(sid)
        assert not dest._admitted
    finally:
        dest.unregister()


def test_admin_delegation_uses_agent_workplace_from_personal_chat(tmp_path):
    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    try:
        store.update_agent("ops", {"workplace_id": "wp_ops", "workplace_scope": "single"})
        sid = store.create_home_session("usr_admin")["session_id"]
        parent = store.access.resolve_context("usr_admin", sid)
        child = store.access.resolve_context("usr_admin", sid, "ops", parent=parent)
        assert child.active_workplace_id == "wp_ops"
        with execution_scope(child):
            assert "delegated-ok" in try_remote("exec_bash", {"script": "echo delegated-ok"})
        assert store.get_session(sid)["workplace_id"] == parent.active_workplace_id
    finally:
        dest.unregister()


def test_admin_routing_rechecks_revoked_destination(tmp_path):
    from app.runtime.access import AccessDenied

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        context = store.access.resolve_context("usr_admin", sid)
        store.access.revoke("usr_admin", "usr_admin", "unrestricted", "wp_ops")
        with execution_scope(context), pytest.raises(AccessDenied):
            try_remote("exec_bash", {"script": "echo forbidden"}, workplace_hint="ops-host")
        assert not dest._admitted
    finally:
        dest.unregister()


def test_admin_personal_chat_routes_to_local_workplace(tmp_path):
    from app.runtime.tools.registry import get_registry

    _rebind(tmp_path)
    target = tmp_path / "local-target"
    target.mkdir()
    store.create_workplace({"id": "wp_local", "name": "local-target", "kind": "local", "root_path": str(target)})
    sid = store.create_home_session("usr_admin")["session_id"]
    original = store.get_session(sid)["workplace_id"]
    with execution_scope(store.access.resolve_context("usr_admin", sid)):
        out = get_registry().execute("bash", {"command": "pwd", "workplace": "local-target"})
    assert out.strip() == str(target)
    assert store.get_session(sid)["workplace_id"] == original


def test_admin_personal_chat_routes_to_ssh_workplace(tmp_path):
    from app.runtime.tools.registry import get_registry

    _rebind(tmp_path)
    store.create_workplace({"id": "wp_ssh", "name": "ssh-target", "kind": "ssh",
                            "ssh_host": "h", "ssh_user": "u", "ssh_password": "p"})
    store.with_db(lambda c: (c.execute("UPDATE workplaces SET status='connected' WHERE id='wp_ssh'"), c.commit()))
    sid = store.create_home_session("usr_admin")["session_id"]
    original = store.get_session(sid)["workplace_id"]
    with execution_scope(store.access.resolve_context("usr_admin", sid)):
        with patch("app.workplaces.ssh_exec.call", return_value={"ok": True, "result": {"stdout": "ssh-routed"}}) as call:
            out = get_registry().execute("bash", {"command": "hostname", "workplace": "ssh-target"})
        assert "ssh-routed" in out
        assert call.call_args.args[0]["id"] == "wp_ssh"
    assert store.get_session(sid)["workplace_id"] == original


def test_delegated_workplace_overrides_inherited_chat_default(tmp_path):
    from app.runtime.tools.workplace_ctx import bind_workplace, reset_workplace

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    try:
        store.update_agent("ops", {"workplace_id": "wp_ops"})
        sid = store.create_home_session("usr_admin")["session_id"]
        parent = store.access.resolve_context("usr_admin", sid)
        child = store.access.resolve_context("usr_admin", sid, "ops", parent=parent)
        tokens = bind_workplace(workplace_id=parent.active_workplace_id)
        try:
            with execution_scope(child):
                out = try_remote("exec_bash", {"script": "echo agent-target"})
            assert out is not None and "agent-target" in out
        finally:
            reset_workplace(tokens)
    finally:
        dest.unregister()


def test_background_job_keeps_routed_destination(tmp_path):
    from app.services.background_jobs import BackgroundJobManager

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    manager = BackgroundJobManager()
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        original = store.get_session(sid)["workplace_id"]
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            job = manager.start("echo bg-routed", workplace_hint="ops-host")
        assert job["workplace_id"] == "wp_ops"
        assert job["execution_context"]["destination_id"] == "wp_ops"
        assert store.get_session(sid)["workplace_id"] == original
    finally:
        manager.stop_session(sid)
        dest.unregister()


@pytest.mark.parametrize("role", ["admin", "member"])
def test_restricted_chat_does_not_gain_implicit_workplace_access(tmp_path, role):
    from app.runtime.access import AccessDenied

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_ops", "ops-host")
    try:
        uid = "usr_admin"
        if role == "member":
            uid = store.create_user({"username": "member_route", "password": "password123"})["id"]
            profile = store.create_llm_profile({"name": "Member", "model": "m"})
            store.access.assign("usr_admin", uid, "model", profile["id"])
            store.access.assign("usr_admin", uid, "workplace", "wp_ops", permission="read_write")
        sid = store.create_home_session(uid)["session_id"]
        active = store.get_session(sid)["workplace_id"]
        store.access.set_chat_access(uid, sid, active, execution_mode="restricted")
        with execution_scope(store.access.resolve_context(uid, sid)), pytest.raises(AccessDenied):
            try_remote("exec_bash", {"script": "echo denied"}, workplace_hint="ops-host")
        assert not dest._admitted
    finally:
        dest.unregister()


def test_admin_routing_respects_specialist_workplace_scope(tmp_path):
    from app.runtime.access import AccessDenied

    _rebind(tmp_path)
    allowed = _online_tunnel(tmp_path, "wp_allowed", "allowed-host")
    other = _online_tunnel(tmp_path, "wp_other", "other-host")
    try:
        store.update_agent("ops", {"workplace_id": "wp_allowed", "workplace_scope": "single"})
        sid = store.create_home_session("usr_admin")["session_id"]
        parent = store.access.resolve_context("usr_admin", sid)
        child = store.access.resolve_context("usr_admin", sid, "ops", parent=parent)
        with execution_scope(child), pytest.raises(AccessDenied):
            try_remote("exec_bash", {"script": "echo denied"}, workplace_hint="other-host")
        assert not other._admitted
    finally:
        allowed.unregister()
        other.unregister()


def test_unused_offline_workplace_does_not_break_admin_personal_chat(tmp_path):
    _rebind(tmp_path)
    store.create_workplace({"id": "wp_offline", "name": "offline", "kind": "tunnel"})
    sid = store.create_home_session("usr_admin")["session_id"]
    context = store.access.resolve_context("usr_admin", sid)
    assert context.active_workplace_id == store.get_session(sid)["workplace_id"]


@pytest.mark.parametrize("tool,args", [
    ("bash", {"command": "printf local-ok"}),
    ("runpy", {"code": "print('local-ok')"}),
])
def test_local_execution_with_remote_resources_in_scope(tmp_path, tool, args):
    from app.runtime.tools.registry import get_registry

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_remote", "remote-host")
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        context = store.access.resolve_context("usr_admin", sid)
        assert any(r.kind == "tunnel" for r in context.resources)
        with execution_scope(context):
            out = get_registry().execute(tool, args)
        assert out.strip() == "local-ok", out
        assert not dest._admitted
    finally:
        dest.unregister()


def test_bash_routes_local_remote_local_without_blocking_host(tmp_path):
    from app.runtime.tools.registry import get_registry

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_remote", "remote-host")
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        context = store.access.resolve_context("usr_admin", sid)
        with execution_scope(context):
            registry = get_registry()
            assert registry.execute("bash", {"command": "printf first-local"}) == "first-local"
            assert registry.execute("bash", {"command": "echo on-remote", "workplace": "remote-host"}) == "on-remote"
            assert registry.execute("bash", {"command": "printf back-local"}) == "back-local"
        assert store.get_session(sid)["workplace_id"] == context.active_workplace_id
    finally:
        dest.unregister()


@pytest.mark.parametrize("mode,destination", [("unrestricted", "remote"), ("restricted", "local")])
def test_host_backend_rejects_nonlocal_or_restricted_destination(tmp_path, mode, destination):
    from app.runtime.access import AccessDenied
    from app.runtime.isolation import host

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_remote", "remote-host")
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        active = "wp_remote" if destination == "remote" else store.get_session(sid)["workplace_id"]
        store.access.set_chat_access("usr_admin", sid, active, execution_mode=mode)
        context = store.access.resolve_context("usr_admin", sid)
        with execution_scope(context), patch("app.runtime.isolation.host.subprocess.Popen") as spawn:
            with pytest.raises(AccessDenied, match="unrestricted local destination"):
                host.popen(["echo", "must-not-spawn"])
            spawn.assert_not_called()
    finally:
        dest.unregister()


async def test_local_terminal_with_remote_resources_in_scope(tmp_path):
    import asyncio
    from app.services.terminals import TerminalManager

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_remote", "remote-host")
    manager = TerminalManager()
    try:
        sid = store.create_home_session("usr_admin")["session_id"]
        context = store.access.resolve_context("usr_admin", sid)
        active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
        terminal = manager.create_unrestricted(context, Path(active.root_path), 80, 24)
        await terminal.write("printf '__%s__\\n' terminal-ok\n")
        async with asyncio.timeout(5):
            while b"__terminal-ok__" not in b"".join(terminal.output):
                await asyncio.sleep(0.01)
        assert terminal.process.poll() is None
        assert not dest._admitted
    finally:
        await manager.close_all()
        dest.unregister()


async def test_local_background_command_with_remote_resources_in_scope(tmp_path):
    import asyncio
    from app.services.background_jobs import BackgroundJobManager

    _rebind(tmp_path)
    dest = _online_tunnel(tmp_path, "wp_remote", "remote-host")
    manager = BackgroundJobManager()
    sid = store.create_home_session("usr_admin")["session_id"]
    try:
        context = store.access.resolve_context("usr_admin", sid)
        with execution_scope(context):
            job = manager.start("printf background-local-ok")
        async with asyncio.timeout(5):
            while job["status"] not in {"succeeded", "failed", "stopped"}:
                await asyncio.sleep(0.01)
                job = store.get_background_job(job["id"])
        assert job["status"] == "succeeded", job.get("stderr")
        assert "background-local-ok" in job["stdout"]
        assert not dest._admitted
    finally:
        manager.stop_session(sid)
        dest.unregister()

"""Tunnel tool routing through verified destinations (no transport-only mocks)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.access import execution_scope
from app.runtime.tools import bash, read_file, runpy, sandbox, write_file
from app.runtime.tools.registry import reset_registry
from app.runtime.tools.tunnel_rpc import format_rpc_result
from app.services import store
from app.workplaces import remote_contract
from app.workplaces.hub import hub
from tests.fakes.remote import FakeDestination


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path) -> None:
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "tunnel-tools.db")
    ensure_stoppers()
    hub.reset()
    remote_contract.reset()
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    hub.reset()
    remote_contract.reset()
    reset_registry()


def _admitted_tunnel(tmp_path: Path, *, mode: str = "unrestricted"):
    """Real admission: paired 0.4.0 tunnel + enforcing destination + grants."""
    wp = store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    store.pair_connector(wp["pairing_code"], hostname="dev", version="0.4.0")
    dest = FakeDestination(
        "wp_tun", tmp_path / "dest",
        caps="idempotent-replay,exec-stream,exec-context-v1,remote-sandbox-v1",
        sandbox_image="sha256:test",
    )
    dest.register()
    store.update_agent("ops", {"workplace_id": "wp_tun"})
    sandbox.bind_agent("ops")
    store.access.assign("usr_admin", "usr_admin", "workplace", "wp_tun", permission="read_write")
    ack = mode == "unrestricted"
    if ack:
        store.access.assign("usr_admin", "usr_admin", "unrestricted", "wp_tun")
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.set_chat_access("usr_admin", sid, "wp_tun", execution_mode=mode,
                                 unrestricted_acknowledged=ack)
    return sid, dest


def test_tunnel_offline_bash_error(tmp_path) -> None:
    # An offline tunnel in the active chat fails closed at authorization with
    # a clear destination error; nothing executes locally or remotely.
    from app.runtime.access import AccessUnavailable
    store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.set_chat_access("usr_admin", sid, "wp_tun", execution_mode="restricted")
    with pytest.raises(AccessUnavailable, match="(?i)offline"):
        store.access.resolve_context("usr_admin", sid)


def test_tunnel_offline_read_write_error(tmp_path) -> None:
    from app.runtime.access import AccessUnavailable
    store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.set_chat_access("usr_admin", sid, "wp_tun", execution_mode="restricted")
    with pytest.raises(AccessUnavailable, match="(?i)offline"):
        store.access.resolve_context("usr_admin", sid)


def test_format_exec_bash_result() -> None:
    text = format_rpc_result(
        "exec_bash",
        {"stdout": "hi\n", "stderr": "warn", "exit_code": 1},
    )
    assert "hi" in text
    assert "stderr" in text
    assert "1" in text


def test_tunnel_rpc_exec_bash_via_verified_destination(tmp_path: Path) -> None:
    sid, dest = _admitted_tunnel(tmp_path)
    try:
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            result = bash.run({"command": "pwd && echo hello-tunnel"})
        assert "hello-tunnel" in result
        assert not result.startswith("Error:")
        # Destination-owned scope dir, not coordinator paths.
        assert "wp_tun" in result
    finally:
        dest.unregister()


def test_tunnel_rpc_exec_python_via_verified_destination(tmp_path: Path) -> None:
    sid, dest = _admitted_tunnel(tmp_path)
    try:
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            result = runpy.run({"code": "print(42)"})
        assert "42" in result
    finally:
        dest.unregister()


def test_tunnel_rpc_files_via_verified_destination(tmp_path: Path) -> None:
    sid, dest = _admitted_tunnel(tmp_path)
    try:
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            w = write_file.run({"path": "note.txt", "content": "hi"})
            r = read_file.run({"path": "note.txt"})
            from app.runtime.tools import delete_file, str_replace

            s = str_replace.run(
                {"path": "note.txt", "old_string": "hi", "new_string": "yo"}
            )
            d = delete_file.run({"path": "note.txt"})
        assert "Wrote" in w
        assert "1|hi" in r  # line-numbered read format
        assert "Replaced" in s
        assert "Deleted" in d
    finally:
        dest.unregister()


def test_tunnel_background_process_start(tmp_path: Path) -> None:
    import time
    sid, dest = _admitted_tunnel(tmp_path)
    try:
        from app.services.background_jobs import manager
        with execution_scope(store.access.resolve_context("usr_admin", sid)):
            result = bash.run({"command": "sleep 60", "background": True})
        assert "Started background job" in result
        job = store.list_background_jobs(sid)[0]
        assert job["id"] in result and job["backend"] == "tunnel"
        assert job["backend_handle"].startswith("job_")
        # Destination attests the supervised v2 contract, not bare transport.
        assert dest._jobs[job["backend_handle"]].owner == "usr_admin"
        stopped = manager.stop_job(sid, job["id"], user_id="usr_admin")
        assert stopped["status"] in ("stopped", "stopping", "succeeded", "failed", "unknown")
        deadline = time.time() + 5
        while dest._jobs[job["backend_handle"]].proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert dest._jobs[job["backend_handle"]].proc.poll() is not None
    finally:
        dest.unregister()


def test_local_workplace_unchanged(tmp_path: Path) -> None:
    # The chat's active workplace selects the shell cwd, not legacy agent
    # bindings: an explicitly activated local folder is the execution root.
    from app.runtime.access import execution_scope
    from tests.fakes.access import ensure_stoppers
    ensure_stoppers()
    root = tmp_path / "local-root"
    root.mkdir()
    store.create_workplace(
        {"id": "wp_local", "name": "L", "kind": "local", "root_path": str(root)}
    )
    sid = store.create_swarm_session(["main"], user_id="usr_admin")
    store.access.assign("usr_admin", "usr_admin", "unrestricted", "wp_local")
    store.access.set_chat_access("usr_admin", sid, "wp_local",
                                 execution_mode="unrestricted",
                                 unrestricted_acknowledged=True)
    with execution_scope(store.access.resolve_context("usr_admin", sid)):
        result = bash.run({"command": "pwd && echo local-ok"})
    assert "local-ok" in result
    assert str(root.resolve()) in result


def test_local_runpy() -> None:
    from tests.fakes.access import owned_admin_scope
    sandbox.bind_agent("ops")
    with owned_admin_scope():
        result = runpy.run({"code": "print('py-ok')"})
    assert "py-ok" in result

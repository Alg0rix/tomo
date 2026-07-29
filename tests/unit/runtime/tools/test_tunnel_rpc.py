"""Tunnel tool routing: offline error + mock hub RPC."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.runtime.tools import bash, read_file, runpy, sandbox, write_file
from app.runtime.tools.registry import execute, reset_registry
from app.runtime.tools.tunnel_rpc import format_rpc_result
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


def test_format_exec_bash_result() -> None:
    text = format_rpc_result(
        "exec_bash",
        {"stdout": "hi\n", "stderr": "warn", "exit_code": 1},
    )
    assert "hi" in text
    assert "stderr" in text
    assert "1" in text


def test_tunnel_rpc_exec_bash_via_mock_hub() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        assert wid == "wp_tun"
        assert method == "exec_bash"
        assert params["script"] == "pwd && echo hello-tunnel"
        return {
            "ok": True,
            "result": {
                "stdout": "/remote/cwd\nhello-tunnel\n",
                "stderr": "",
                "exit_code": 0,
                "execution_time": 0.01,
            },
        }

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        result = bash.run({"command": "pwd && echo hello-tunnel"})
    assert "hello-tunnel" in result
    assert not result.startswith("Error:")


def test_tunnel_rpc_exec_python_via_mock_hub() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        assert method == "exec_python"
        assert "print" in params["code"]
        return {
            "ok": True,
            "result": {
                "stdout": "42\n",
                "stderr": "",
                "exit_code": 0,
                "execution_time": 0.02,
            },
        }

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        result = runpy.run({"code": "print(42)"})
    assert "42" in result


def test_tunnel_rpc_files_via_mock_hub() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        if method == "write_file":
            assert params["path"] == "note.txt"
            assert params["content"] == "hi"
            return {"ok": True, "result": {"ok": True, "path": "/remote/note.txt"}}
        if method == "read_file":
            return {
                "ok": True,
                "result": {
                    "content": "hi",
                    "size": 2,
                    "path": "/remote/note.txt",
                },
            }
        if method == "str_replace":
            return {"ok": True, "result": {"ok": True, "path": "/remote/note.txt"}}
        if method == "delete_file":
            return {"ok": True, "result": {"ok": True, "path": "/remote/note.txt"}}
        return {"ok": False, "error": f"unexpected {method}"}

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        w = write_file.run({"path": "note.txt", "content": "hi"})
        r = read_file.run({"path": "note.txt"})
        from app.runtime.tools import delete_file, str_replace

        s = str_replace.run(
            {"path": "note.txt", "old_string": "hi", "new_string": "yo"}
        )
        d = delete_file.run({"path": "note.txt"})
    assert "Wrote" in w or "ok" in w.lower() or "Created" in w
    assert "1|hi" in r  # line-numbered read format
    assert "Replaced" in s
    assert "Deleted" in d


def test_tunnel_background_process_start() -> None:
    _tunnel_agent()

    def _fake_call(wid, method, params=None, timeout=60.0):
        assert method == "process_start"
        return {"ok": True, "result": {"id": "job_9", "status": "running"}}

    with patch.object(hub, "is_online", return_value=True), patch.object(
        hub, "call", side_effect=_fake_call
    ):
        result = bash.run({"command": "sleep 99", "background": True})
    assert "job_9" in result


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


def test_local_runpy() -> None:
    sandbox.bind_agent("ops")
    result = runpy.run({"code": "print('py-ok')"})
    assert "py-ok" in result

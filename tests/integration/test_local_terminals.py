"""Real PTYs through the browser API: ownership, reattachment and cleanup."""

import asyncio
import json
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.services import store

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Local PTYs require POSIX")
ORIGIN = {"origin": "http://testserver"}


def login(client, name):
    response = client.post(
        "/login",
        data={"username": name, "password": "password1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def output_until(ws, marker):
    output = b""
    while marker not in output:
        message = ws.receive()
        if message.get("bytes"):
            output += message["bytes"]
    return output


def _admin_local_session(uid, wid):
    # Explicitly unrestricted local execution (Admin or granted Member)
    # uses supervised host PTYs; restricted chats use held container
    # terminals instead (see test_multi_user_member_terminals).
    store.access.assign("usr_admin", uid, "unrestricted", wid)
    sid = store.create_swarm_session(["main"], user_id=uid)
    store.access.set_chat_access(uid, sid, wid, execution_mode="unrestricted",
                                 unrestricted_acknowledged=True)
    return sid


def test_local_terminal_sessions_reattach_and_cleanup(tmp_path):
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "terminals.db")
    ensure_stoppers()
    alice = store.create_user({"username": "terminal_alice", "password": "password1", "role": "admin"})
    store.create_user({"username": "terminal_bob", "password": "password1"})
    local_folder = tmp_path / "local-project"
    local_folder.mkdir()
    local = store.create_workplace(
        {"name": "Terminal local", "kind": "local", "root_path": str(local_folder)}
    )
    other_folder = tmp_path / "other-project"
    other_folder.mkdir()
    other_local = store.create_workplace(
        {"name": "Terminal other", "kind": "local", "root_path": str(other_folder)}
    )
    remote = store.create_workplace(
        {"name": "Terminal remote", "kind": "tunnel", "root_path": "/remote/project"}
    )
    sid = _admin_local_session(alice["id"], local["id"])
    other_sid = _admin_local_session(alice["id"], other_local["id"])
    remote_sid = _admin_local_session(alice["id"], remote["id"])
    base = f"/api/sessions/{sid}/terminals"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        login(client, "terminal_alice")
        assert (
            client.post(
                base, json={}, headers={"origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(base, json={}, headers={"origin": "http://["}).status_code
            == 403
        )
        first = client.post(base, json={}).json()
        second = client.post(base, json={}).json()
        assert first["id"] != second["id"]
        assert first["cwd"] == str(local_folder)
        assert (
            client.get(f"/api/sessions/{other_sid}/terminals").json()["terminals"] == []
        )
        assert (
            client.delete(
                f"/api/sessions/{other_sid}/terminals/{first['id']}"
            ).status_code
            == 404
        )
        # A tunnel-active chat must never route a terminal through its
        # connector: remote interactive backends are unavailable, so creation
        # is refused instead of silently falling back to a local shell.
        assert client.post(
            f"/api/sessions/{remote_sid}/terminals", json={}
        ).status_code == 503
        url = base + "/" + first["id"] + "/ws"
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(url, headers={"origin": "https://evil.test"}),
        ):
            pass
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "resize", "cols": 101, "rows": 31})
            ws.send_json(
                {
                    "type": "input",
                    "data": "export CHAT_VALUE=kept; stty size; printf '\\n__%s__\\n' ready\r",
                }
            )
            output = output_until(ws, b"__ready__")
            assert b"31 101" in output
        # A disconnected browser keeps the same PID, environment and scrollback.
        assert client.get(base).json()["terminals"][0]["pid"] == first["pid"]
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            assert ws.receive_json()["pid"] == first["pid"]
            assert b"__ready__" in ws.receive_bytes()
            ws.send_json(
                {
                    "type": "input",
                    "data": "printf '__value:%s__\\n' \"$CHAT_VALUE\"; sleep 600 & printf '__job:%s__\\n' \"$!\"; printf '__%s__\\n' done\r",
                }
            )
            output = output_until(ws, b"__done__")
            assert b"__value:kept__" in output
            job_pid = int(re.search(rb"__job:(\d+)__", output)[1])
        login(client, "terminal_bob")
        assert client.get(base).status_code == 404
        assert client.post(base, json={}).status_code == 404
        assert client.delete(base + "/" + first["id"]).status_code == 404
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(url, headers=ORIGIN),
        ):
            pass
        login(client, "terminal_alice")
        assert client.delete(base + "/" + first["id"]).status_code == 200
        assert len(client.get(base).json()["terminals"]) == 1
        # A stopped background process may briefly be a zombie awaiting init.
        proc = f"/proc/{job_pid}/stat"
        if os.path.exists(proc):
            assert Path(proc).read_text().split(") ", 1)[1].startswith("Z")
        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        with pytest.raises(ProcessLookupError):
            os.kill(second["pid"], 0)


def test_idle_cleanup_spares_foreground_commands(tmp_path, monkeypatch):
    import app.services.terminals as terminals

    from tests.fakes.access import ensure_stoppers
    monkeypatch.setattr(terminals, "IDLE_TIMEOUT", 1)
    store.rebind(tmp_path / "terminal-idle.db")
    ensure_stoppers()
    user = store.create_user({"username": "terminal_idle", "password": "password1", "role": "admin"})
    idle_folder = tmp_path / "idle-project"
    idle_folder.mkdir()
    wid = store.create_workplace(
        {"name": "Terminal idle", "kind": "local", "root_path": str(idle_folder)}
    )["id"]
    sid = _admin_local_session(user["id"], wid)
    base = f"/api/sessions/{sid}/terminals"
    with TestClient(app) as client:
        login(client, "terminal_idle")
        idle = client.post(base, json={}).json()
        busy = client.post(base, json={}).json()
        with client.websocket_connect(
            base + "/" + busy["id"] + "/ws", headers=ORIGIN
        ) as ws:
            ws.receive_json()
            ws.send_json(
                {"type": "input", "data": "sleep 9; printf '__%s__\\n' finished\r"}
            )
            deadline = time.monotonic() + 8
            remaining = client.get(base).json()["terminals"]
            while len(remaining) > 1 and time.monotonic() < deadline:
                time.sleep(0.1)
                remaining = client.get(base).json()["terminals"]
            assert [t["id"] for t in remaining] == [busy["id"]]
            assert remaining[0]["busy"]
            assert b"__finished__" in output_until(ws, b"__finished__")
        deadline = time.monotonic() + 8
        while client.get(base).json()["terminals"] and time.monotonic() < deadline:
            time.sleep(0.2)
        assert client.get(base).json()["terminals"] == []
        for pid in (idle["pid"], busy["pid"]):
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


async def test_cancelled_close_finishes_cleanup(tmp_path, monkeypatch):
    from app.services.terminals import TerminalManager
    from tests.fakes.access import ensure_stoppers

    store.rebind(tmp_path / "terminal-cancel.db")
    ensure_stoppers()
    user = store.create_user({"username": "terminal_cancel", "password": "password1", "role": "admin"})
    cancel_folder = tmp_path / "cancel-project"
    cancel_folder.mkdir()
    wid = store.create_workplace(
        {"name": "Terminal cancel", "kind": "local", "root_path": str(cancel_folder)}
    )["id"]
    sid = _admin_local_session(user["id"], wid)
    manager = TerminalManager()
    terminal = manager.create(sid, tmp_path, 80, 24, user_id=user["id"])
    started = asyncio.Event()
    release = asyncio.Event()
    cleanup = terminal._cleanup

    async def delayed_cleanup(reason):
        started.set()
        await release.wait()
        await cleanup(reason)

    monkeypatch.setattr(terminal, "_cleanup", delayed_cleanup)
    try:
        closing = asyncio.create_task(manager.close(sid, terminal.id, user_id=user["id"]))
        await asyncio.wait_for(started.wait(), timeout=2)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        # Still counted while cleanup is in flight; cancellation cannot free a slot early.
        assert manager.get(sid, terminal.id, user_id=user["id"]) is terminal
        release.set()
        async with asyncio.timeout(3):
            while manager.list(sid, user_id=user["id"]):
                await asyncio.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(terminal.process.pid, 0)
    finally:
        release.set()
        await manager.close_all()


def test_terminal_limits_validation_shell_exit_and_draft_pruning(tmp_path, monkeypatch):
    import app.services.terminals as terminals

    from tests.fakes.access import ensure_stoppers
    monkeypatch.setattr(terminals, "MAX_PER_SESSION", 2)
    store.rebind(tmp_path / "terminal-limits.db")
    ensure_stoppers()
    user = store.create_user({"username": "terminal_limits", "password": "password1", "role": "admin"})
    limits_folder = tmp_path / "limits-project"
    limits_folder.mkdir()
    wid = store.create_workplace(
        {"name": "Terminal limits", "kind": "local", "root_path": str(limits_folder)}
    )["id"]
    sid = _admin_local_session(user["id"], wid)
    base = f"/api/sessions/{sid}/terminals"
    with TestClient(app) as client:
        login(client, "terminal_limits")
        assert client.post(base, json={"cols": 0}).status_code == 422
        first = client.post(base, json={}).json()
        second = client.post(base, json={}).json()
        assert client.post(base, json={}).status_code == 409
        url = base + "/" + first["id"] + "/ws"
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "input", "data": "exit 7\r"})
            while True:
                message = ws.receive()
                if message.get("text"):
                    event = json.loads(message["text"])
                    if event["type"] == "exit":
                        assert event["exit_code"] == 7
                        break
        assert client.get(base).json()["terminals"][0]["exit_code"] == 7
        assert client.delete(base + "/" + first["id"]).status_code == 200
        assert client.post(base, json={}).status_code == 201
        pruned = client.post("/api/sessions/prune-drafts").json()["deleted"]
        assert sid in pruned
        # The pruned session no longer resolves, so list() cannot authorize
        # it; assert directly that no retained handle references the session.
        assert all(
            t.session_id != sid
            for t in terminals.terminal_manager.terminals.values()
        )
        with pytest.raises(ProcessLookupError):
            os.kill(second["pid"], 0)

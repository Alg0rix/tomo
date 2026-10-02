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


def test_local_terminal_sessions_reattach_and_cleanup(tmp_path):
    store.rebind(tmp_path / "terminals.db")
    alice = store.create_user({"username": "terminal_alice", "password": "password1"})
    store.create_user({"username": "terminal_bob", "password": "password1"})
    local_folder = tmp_path / "local-project"
    local_folder.mkdir()
    local = store.create_workplace(
        {"name": "Terminal local", "kind": "local", "root_path": str(local_folder)}
    )
    remote = store.create_workplace(
        {"name": "Terminal remote", "kind": "tunnel", "root_path": "/remote/project"}
    )
    sid = store.create_swarm_session(
        ["main"], user_id=alice["id"], workplace_id=local["id"]
    )
    other_sid = store.create_swarm_session(
        ["main"], user_id=alice["id"], workplace_id=remote["id"]
    )
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
        # A tunnel workplace must still create a local shell without a connector.
        remote_context = client.post(
            f"/api/sessions/{other_sid}/terminals", json={}
        ).json()
        assert remote_context["cwd"].endswith(f"/sessions/{other_sid}/workspace")
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
    with pytest.raises(ProcessLookupError):
        os.kill(remote_context["pid"], 0)


def test_idle_cleanup_spares_foreground_commands(tmp_path, monkeypatch):
    import app.services.terminals as terminals

    monkeypatch.setattr(terminals, "IDLE_TIMEOUT", 1)
    store.rebind(tmp_path / "terminal-idle.db")
    user = store.create_user({"username": "terminal_idle", "password": "password1"})
    sid = store.create_swarm_session(["main"], user_id=user["id"])
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

    manager = TerminalManager()
    terminal = manager.create("cancelled", tmp_path, 80, 24)
    started = asyncio.Event()
    release = asyncio.Event()
    cleanup = terminal._cleanup

    async def delayed_cleanup(reason):
        started.set()
        await release.wait()
        await cleanup(reason)

    monkeypatch.setattr(terminal, "_cleanup", delayed_cleanup)
    try:
        closing = asyncio.create_task(manager.close("cancelled", terminal.id))
        await asyncio.wait_for(started.wait(), timeout=2)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        # Still counted while cleanup is in flight; cancellation cannot free a slot early.
        assert manager.get("cancelled", terminal.id) is terminal
        release.set()
        async with asyncio.timeout(3):
            while manager.list("cancelled"):
                await asyncio.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.kill(terminal.process.pid, 0)
    finally:
        release.set()
        await manager.close_all()


def test_terminal_limits_validation_shell_exit_and_draft_pruning(tmp_path, monkeypatch):
    import app.services.terminals as terminals

    monkeypatch.setattr(terminals, "MAX_PER_SESSION", 2)
    store.rebind(tmp_path / "terminal-limits.db")
    user = store.create_user({"username": "terminal_limits", "password": "password1"})
    sid = store.create_swarm_session(["main"], user_id=user["id"])
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
        assert terminals.terminal_manager.list(sid) == []
        with pytest.raises(ProcessLookupError):
            os.kill(second["pid"], 0)

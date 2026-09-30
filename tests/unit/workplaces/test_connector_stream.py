"""Live bash output over a tunnel: real /api/connector/ws with a fake connector."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connector import router
from app.runtime.tools import bash, progress, sandbox
from app.services import store
from app.workplaces.hub import hub
from app.workplaces.pairing import rate_limiter


@pytest.fixture()
def paired(tmp_path: Path):
    store.rebind(tmp_path / "stream.db")
    hub.reset()
    rate_limiter.reset()
    store.create_workplace({"id": "wp_tun", "name": "T", "kind": "tunnel"})
    token = store.pair_connector(store.get_workplace("wp_tun")["pairing_code"])["token"]
    store.update_agent("ops", {"workplace_id": "wp_tun"})
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), token
    sandbox.reset_agent()
    hub.reset()


def _run_bash_in_thread(command: str) -> tuple[threading.Thread, dict]:
    box: dict = {"chunks": []}

    def target() -> None:
        sandbox.bind_agent("ops")
        token = progress.bind(lambda c: box["chunks"].append((time.monotonic(), c)))
        try:
            box["result"] = bash.run({"command": command})
            box["done_at"] = time.monotonic()
        finally:
            progress.reset(token)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def _connect(client: TestClient, token: str, caps: str):
    ws = client.websocket_connect(
        "/api/connector/ws",
        headers={"Authorization": f"Bearer {token}", "X-Tomo-Caps": caps},
    )
    conn = ws.__enter__()
    assert conn.receive_json()["type"] == "hello_ok"
    return ws, conn


def test_tunnel_bash_streams_progress_before_result(paired) -> None:
    client, token = paired
    ws, conn = _connect(client, token, "idempotent-replay,exec-stream")
    try:
        thread, box = _run_bash_in_thread("make build")
        req = conn.receive_json()
        assert req["type"] == "rpc_request" and req["method"] == "exec_bash"
        assert req["params"]["stream"] is True

        conn.send_json({"v": 1, "type": "rpc_progress", "id": req["id"], "result": {"data": "step 1\n"}})
        deadline = time.monotonic() + 2
        while not box["chunks"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [c for _, c in box["chunks"]] == ["step 1\n"]
        assert "result" not in box  # still running: the chunk arrived live

        # Another workplace's id space: unknown ids are ignored, not delivered.
        conn.send_json({"v": 1, "type": "rpc_progress", "id": "someone-else", "result": {"data": "x"}})
        conn.send_json({"v": 1, "type": "rpc_progress", "id": req["id"], "result": {"data": "step 2\n"}})
        conn.send_json({
            "v": 1, "type": "rpc_response", "id": req["id"], "ok": True,
            "result": {"stdout": "step 1\nstep 2\n", "stderr": "", "exit_code": 0},
        })
        thread.join(3)
    finally:
        ws.__exit__(None, None, None)

    assert box["result"] == "step 1\nstep 2"
    assert "".join(c for _, c in box["chunks"]) == "step 1\nstep 2\n"
    assert all(ts <= box["done_at"] for ts, _ in box["chunks"])


def test_old_connector_without_stream_cap_gets_plain_request(paired) -> None:
    client, token = paired
    ws, conn = _connect(client, token, "idempotent-replay")
    try:
        thread, box = _run_bash_in_thread("echo hi")
        req = conn.receive_json()
        assert "stream" not in req["params"]
        conn.send_json({
            "v": 1, "type": "rpc_response", "id": req["id"], "ok": True,
            "result": {"stdout": "hi\n", "stderr": "", "exit_code": 0},
        })
        thread.join(3)
    finally:
        ws.__exit__(None, None, None)
    assert box["result"] == "hi"
    assert box["chunks"] == []

"""Real HTTP/SSE authorization, filtering, rotation and access revocation."""
import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.logs import router
from app.services import store


@pytest.mark.asyncio
async def test_admin_log_history_and_live_stream(tmp_path, monkeypatch):
    store.rebind(tmp_path / "store.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("TOMO_LOG_DIR", str(log_dir))
    path = log_dir / "tomo.jsonl"
    record = {"timestamp": "2026-01-01T00:00:00+00:00", "level": "ERROR", "type": "plugin", "message": "hook failed", "session_id": "s1"}
    path.write_text(json.dumps(record) + "\n")
    admin = store.create_user({"username": "logadmin", "password": "password1", "role": "admin"})
    member = store.create_user({"username": "logmember", "password": "password1", "role": "member"})
    admin_key = store.create_api_key(admin["id"])["token"]
    member_key = store.create_api_key(member["id"])["token"]
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(router)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", access_log=False, log_level="critical"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            for endpoint in ("/api/logs", "/api/logs/stream"):
                assert (await client.get(endpoint)).status_code == 401
                assert (await client.get(endpoint, headers={"Authorization": f"Bearer {member_key}"})).status_code == 403
            headers = {"Authorization": f"Bearer {admin_key}"}
            response = await client.get("/api/logs?type=plugin&session=s1&level=ERROR", headers=headers)
            assert [item["record"] for item in response.json()["records"]] == [record]
            assert (await client.get("/api/logs?type=llm", headers=headers)).json()["records"] == []
            assert (await client.get("/api/logs?tail=501", headers=headers)).status_code == 422
            async with client.stream("GET", "/api/logs/stream?type=plugin&tail=0", headers=headers) as response:
                assert response.headers["content-type"].startswith("text/event-stream")
                lines = response.aiter_lines()
                assert await anext(lines) == "event: snapshot"
                assert await anext(lines) == "data: []"
                path.rename(log_dir / "tomo.jsonl.1")
                next_record = {**record, "message": "after rotation"}
                path.write_text(json.dumps(next_record) + "\n")
                async with asyncio.timeout(5):
                    while True:
                        line = await anext(lines)
                        if line.startswith("data: "):
                            assert json.loads(line[6:])["record"] == next_record
                            break
                store.update_user(admin["id"], {"enabled": False})
                async with asyncio.timeout(5):
                    while await anext(lines) != "event: forbidden":
                        pass
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()

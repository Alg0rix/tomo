"""Consolidated tests (merged from: test_openai_compat_api.py, test_mcp_api.py, test_background_jobs_api.py).
- test_openai_compat_api.py: Integration: OpenAI-compat completions + session POST SSE.
- test_mcp_api.py: MCP server API: CRUD, discovery, item toggle, resource/prompt actions.
- test_background_jobs_api.py: Job snapshots and controls must remain private even to shared-chat admins.
"""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient
from app.core.deps import require_auth
from app.main import app
from app.runtime.llm.mock import MockLLMClient
from app.services import store
from contextlib import AsyncExitStack
from types import SimpleNamespace
from mcp import types as mcp_types
from app.runtime.mcp import mcp_manager
import asyncio
import time


# --- from test_openai_compat_api.py ---
@pytest.fixture(autouse=True)
def _inject_mock_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: MockLLMClient(),
    )


def _auth_client(tmp_path, db_name: str) -> tuple[TestClient, str]:
    store.rebind(tmp_path / db_name)
    admin = store.get_user_by_username("admin")
    token = store.create_api_key(admin["id"], "openai-test")["token"]
    return TestClient(app), token


def _parse_openai_sse(raw: str) -> list[dict | str]:
    items: list[dict | str] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                items.append("[DONE]")
            else:
                items.append(json.loads(payload))
    return items


def _parse_tomo_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name: str | None = None
        data = "{}"
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].lstrip()
        if name:
            events.append((name, json.loads(data)))
    return events


def test_chat_completions_requires_auth(tmp_path) -> None:
    store.rebind(tmp_path / "oai_auth.db")
    client = TestClient(app)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 401


def test_chat_completions_unknown_model(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_model.db")
    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "no_such_agent",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["type"] == "not_found_error"


def test_chat_completions_non_stream(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_nonstrm.db")
    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello there"}],
            "stream": False,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "main"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"
    sid = res.headers.get("X-Tomo-Session-Id")
    assert sid
    assert store.get_session(sid)


def test_chat_completions_stream(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_strm.db")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello stream"}],
            "stream": True,
        },
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        sid = res.headers.get("X-Tomo-Session-Id")
        assert sid
        raw = "".join(res.iter_text())

    items = _parse_openai_sse(raw)
    assert items[-1] == "[DONE]"
    chunks = [i for i in items if isinstance(i, dict)]
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    contents = [
        c["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert "".join(contents)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_session_chat_stream_post(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "sess_post_strm.db")
    admin = store.get_user_by_username("admin")
    sid = store.get_or_create_session("main", admin["id"])

    with client.stream(
        "POST",
        f"/api/sessions/{sid}/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello post stream"},
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        raw = "".join(res.iter_text())

    events = _parse_tomo_sse(raw)
    names = [n for n, _ in events]
    assert "delta" in names or "done" in names
    assert "turn.end" in names


def test_session_chat_stream_post_requires_message(tmp_path) -> None:
    store.rebind(tmp_path / "sess_post_empty.db")
    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        res = client.post(
            f"/api/sessions/{sid}/chat/stream",
            json={"message": "  "},
        )
        assert res.status_code == 400
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_chat_completions_continues_swarm_via_session_header(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_swarm_hdr.db")
    admin = store.get_user_by_username("admin")
    sid = store.create_swarm_session(
        ["main", "ops", "coder", "research"],
        user_id=admin["id"],
    )
    session = store.get_session(sid)
    assert len(session.get("agent_ids") or []) >= 2

    res = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tomo-Session-Id": sid,
        },
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello swarm"}],
            "stream": False,
        },
    )
    assert res.status_code == 200
    assert res.headers.get("X-Tomo-Session-Id") == sid
    assert res.json()["choices"][0]["message"]["content"]
    # Must not have created a separate solo session for this turn.
    assert store.get_session(sid)["id"] == sid


def test_chat_completions_unknown_session_header(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_bad_sid.db")
    res = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tomo-Session-Id": "ses_does_not_exist",
        },
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["type"] == "not_found_error"


# --- from test_mcp_api.py ---
class FakeSession:
    def __init__(self, *, tools=None, resources=None, prompts=None) -> None:
        self.tools = tools or [mcp_types.Tool(name="echo", inputSchema={"type": "object"})]
        self.resources = resources or [
            mcp_types.Resource(name="r", uri="file:///r.txt", mimeType="text/plain")
        ]
        self.prompts = prompts or [mcp_types.Prompt(name="p", description="a prompt")]

    async def initialize(self):
        return SimpleNamespace(serverInfo=SimpleNamespace(name="fake"), capabilities=SimpleNamespace())

    async def list_tools(self, cursor=None):
        return {"tools": self.tools, "nextCursor": None}

    async def list_resources(self, cursor=None):
        return {"resources": self.resources, "nextCursor": None}

    async def list_resource_templates(self, cursor=None):
        return {"resourceTemplates": [], "nextCursor": None}

    async def list_prompts(self, cursor=None):
        return {"prompts": self.prompts, "nextCursor": None}

    async def call_tool(self, name, arguments):
        return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text="ok")])

    async def read_resource(self, uri):
        return mcp_types.ReadResourceResult(
            contents=[mcp_types.TextResourceContents(uri=uri, text="body", mimeType="text/plain")]
        )

    async def get_prompt(self, name, arguments=None):
        return mcp_types.GetPromptResult(
            description="d",
            messages=[
                mcp_types.PromptMessage(
                    role="user", content=mcp_types.TextContent(type="text", text="hi")
                )
            ],
        )


def _fake_factory(session: FakeSession):
    async def factory(server):
        stack = AsyncExitStack()
        init_result = await session.initialize()
        return stack, session, init_result

    return factory


@pytest.fixture()
def client(tmp_path):
    store.rebind(tmp_path / "mcp-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    mcp_manager.session_factory = _fake_factory(FakeSession())
    yield TestClient(app)
    app.dependency_overrides.pop(require_auth, None)
    mcp_manager.session_factory = None
    mcp_manager._live.clear()
    mcp_manager._connect_locks.clear()


def test_create_saves_and_discovers_with_masked_secrets(client: TestClient) -> None:
    res = client.post(
        "/api/mcp-servers",
        json={
            "id": "gh",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer sekrit-token"},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "connected"
    assert body["headers_keys"] == ["Authorization"]
    assert body["headers_set"] is True
    assert "sekrit-token" not in res.text
    assert "headers_ciphertext" not in body


def test_create_stdio_requires_command(client: TestClient) -> None:
    res = client.post("/api/mcp-servers", json={"name": "x", "transport": "stdio"})
    assert res.status_code == 400


def test_refresh_calls_discovery(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s1", "name": "s1", "transport": "stdio", "command": "echo"},
    )
    assert len(store.list_mcp_items("s1")) == 3  # echo tool + resource + prompt

    res = client.post("/api/mcp-servers/s1/refresh")
    assert res.status_code == 200
    assert res.json()["status"] == "connected"
    assert len(store.list_mcp_items("s1")) == 3


def test_unknown_server_returns_404(client: TestClient) -> None:
    assert client.get("/api/mcp-servers/nope").status_code == 404
    assert client.put("/api/mcp-servers/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/mcp-servers/nope").status_code == 404
    assert client.post("/api/mcp-servers/nope/refresh").status_code == 404


def test_unknown_item_returns_404(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s2", "name": "s2", "transport": "stdio", "command": "echo"},
    )
    res = client.put("/api/mcp-servers/s2/items/nope", json={"enabled": False})
    assert res.status_code == 404


def test_disabled_server_item_toggle_returns_409(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s3", "name": "s3", "transport": "stdio", "command": "echo"},
    )
    item = store.list_mcp_items("s3")[0]
    client.put("/api/mcp-servers/s3", json={"enabled": False})

    res = client.put(f"/api/mcp-servers/s3/items/{item['id']}", json={"enabled": True})
    assert res.status_code == 409


def test_resource_read_and_prompt_get_return_normalized_values(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s4", "name": "s4", "transport": "stdio", "command": "echo"},
    )
    res = client.post("/api/mcp-servers/s4/resources/read", json={"uri": "file:///r.txt"})
    assert res.status_code == 200
    assert res.json()["contents"][0]["text"] == "body"

    res = client.post("/api/mcp-servers/s4/prompts/get", json={"name": "p", "arguments": {}})
    assert res.status_code == 200
    assert res.json()["messages"] == [{"role": "user", "text": "hi"}]


def test_disabled_resource_read_returns_409(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s5", "name": "s5", "transport": "stdio", "command": "echo"},
    )
    item = next(i for i in store.list_mcp_items("s5", kind="resource"))
    store.set_mcp_item_enabled(item["id"], False)

    res = client.post("/api/mcp-servers/s5/resources/read", json={"uri": "file:///r.txt"})
    assert res.status_code == 409


def test_unknown_resource_uri_returns_404(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s6", "name": "s6", "transport": "stdio", "command": "echo"},
    )
    res = client.post("/api/mcp-servers/s6/resources/read", json={"uri": "file:///missing.txt"})
    assert res.status_code == 404


def test_delete_closes_server_and_removes_items(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "s7", "name": "s7", "transport": "stdio", "command": "echo"},
    )
    assert "s7" in mcp_manager.connected_server_ids()
    assert len(store.list_mcp_items("s7")) == 3

    res = client.delete("/api/mcp-servers/s7")
    assert res.status_code == 200
    assert "s7" not in mcp_manager.connected_server_ids()
    assert store.get_mcp_server("s7") is None
    assert store.list_mcp_items("s7") == []


def test_get_server_includes_items_and_no_secret_leak(client: TestClient) -> None:
    res = client.post(
        "/api/mcp-servers",
        json={
            "id": "s8",
            "name": "s8",
            "transport": "stdio",
            "command": "echo",
            "env": {"TOKEN": "super-secret"},
        },
    )
    assert res.status_code == 200
    res = client.get("/api/mcp-servers/s8")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 3
    assert "super-secret" not in res.text
    assert body["env_keys"] == ["TOKEN"]


def test_agent_studio_shows_mcp_source_badge(client: TestClient) -> None:
    client.post(
        "/api/mcp-servers",
        json={"id": "badge_srv", "name": "Badge Server", "transport": "stdio", "command": "echo"},
    )
    res = client.get("/agents/main")
    assert res.status_code == 200
    text = res.text
    assert 'data-tool-id="mcp__badge_srv__echo"' in text
    assert "MCP · Badge Server" in text


def test_system_page_renders_mcp_section(client: TestClient) -> None:
    res = client.get("/system")
    assert res.status_code == 200
    text = res.text
    assert "MCP" in text
    assert 'id="sec-mcp"' in text
    assert 'id="mcpServerList"' in text
    assert 'id="addMcpServerBtn"' in text
    assert 'id="mcpFormCard"' in text
    assert 'id="mcpTransport"' in text


# --- from test_background_jobs_api.py ---
def login(client, username):
    response = client.post(
        "/login",
        data={"username": username, "password": "password1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize('kind', ['session', 'agent'])
async def test_deletion_keeps_other_async_requests_responsive(monkeypatch, kind):
    from app.api import rest
    from app.services.terminals import terminal_manager

    def slow_delete(_id):
        time.sleep(.1)
        return True
    async def close(_sid):
        pass
    monkeypatch.setattr(rest, 'require_owned_session', lambda *args: None)
    monkeypatch.setattr(terminal_manager, 'close_session', close)
    if kind == 'session':
        monkeypatch.setattr(rest.store, 'delete_session', slow_delete)
        task = asyncio.create_task(rest.delete_session_api('sid', None, None))
    else:
        monkeypatch.setattr(rest.store, 'delete_agent', slow_delete)
        task = asyncio.create_task(rest.delete_agent('agent', None))
    await asyncio.sleep(.02)
    assert not task.done(), 'Deletion blocked unrelated event-loop work'
    assert await task == {'success': True}


def test_private_process_api_and_control_isolation(tmp_path, monkeypatch):
    from app.api import processes

    store.rebind(tmp_path / "jobs-api.db")
    alice = store.create_user({"username": "jobs_alice", "password": "password1"})
    store.create_user(
        {"username": "jobs_bob", "password": "password1", "role": "admin"}
    )
    sid = store.create_swarm_session(["main"], user_id=alice["id"])
    sibling = store.create_swarm_session(["main"], user_id=alice["id"])
    # Create records inside lifespan so startup's honest restart recovery does
    # not transform synthetic active jobs before the API assertions.
    with TestClient(app) as client:
        first = store.create_background_job(
            {
                "session_id": sid,
                "user_id": alice["id"],
                "command": "sleep 60",
                "status": "running",
                "backend_handle": "PRIVATE_HANDLE",
                "delivery": {"token": "PRIVATE_TOKEN"},
                "card_message_id": 9876,
                "stdout": "<script>output</script>",
                "stderr": "error",
            }
        )
        second = store.create_background_job(
            {
                "session_id": sid,
                "user_id": alice["id"],
                "command": "sleep 70",
                "status": "running",
            }
        )
        base = f"/api/sessions/{sid}/processes"
        assert client.get(base).status_code == 401
        login(client, "jobs_alice")
        listing = client.get(base).json()["jobs"]
        assert {job["id"] for job in listing} == {first["id"], second["id"]}
        assert all("stdout" not in job for job in listing)
        detail = client.get(base + "/" + first["id"])
        assert detail.status_code == 200
        assert "PRIVATE_" not in detail.text
        assert "backend_handle" not in detail.json()
        assert (
            "delivery" not in detail.json() and "card_message_id" not in detail.json()
        )
        logs = client.get(base + "/" + first["id"] + "/logs").json()
        assert logs["stdout"] == "<script>output</script>"
        assert logs["stderr"] == "error"
        assert client.get(base + "/" + first["id"] + "/logs?tail=0").status_code == 422
        assert (
            client.get(base + "/" + first["id"] + "/logs?tail=1048577").status_code
            == 422
        )
        for suffix in ("", "/logs", "/stop", "/close-monitoring"):
            url = f"/api/sessions/{sibling}/processes/{first['id']}" + suffix
            method = (
                client.post if suffix in ("/stop", "/close-monitoring") else client.get
            )
            assert method(url).status_code == 404
        calls = []

        def stop(session_id, job_id):
            calls.append((session_id, job_id))
            return store.update_background_job(job_id, {"status": "stopping"})

        monkeypatch.setattr(processes.manager, "stop_job", stop)
        for origin in ("https://evil.example", "http://["):
            assert (
                client.post(
                    base + "/" + first["id"] + "/stop", headers={"origin": origin}
                ).status_code
                == 403
            )
        assert calls == []
        stopped = client.post(
            base + "/" + first["id"] + "/stop", headers={"origin": "http://testserver"}
        )
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopping"
        assert calls == [(sid, first["id"])]
        assert store.get_background_job(second["id"])["status"] == "running"
        assert (
            client.post(base + "/" + first["id"] + "/close-monitoring").status_code
            == 409
        )
        store.update_background_job(first["id"], {"status": "unknown"})
        assert (
            client.post(
                base + "/" + first["id"] + "/close-monitoring",
                headers={"origin": "https://evil.example"},
            ).status_code
            == 403
        )
        closed = client.post(base + "/" + first["id"] + "/close-monitoring").json()
        assert closed["status"] == "unknown" and closed["monitoring_closed"]
        assert len(calls) == 1  # Closing monitoring never invokes backend stop.
        login(client, "jobs_bob")
        for suffix in ("", "/" + first["id"], "/" + first["id"] + "/logs"):
            assert client.get(base + suffix).status_code == 404
        assert client.post(base + "/" + first["id"] + "/stop").status_code == 404
        assert (
            client.post(base + "/" + first["id"] + "/close-monitoring").status_code
            == 404
        )



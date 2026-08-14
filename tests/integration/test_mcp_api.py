"""MCP server API: CRUD, discovery, item toggle, resource/prompt actions.

Uses an injected ``mcp_manager.session_factory`` fake session — no test call
ever reaches a real MCP endpoint.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import types as mcp_types

from app.core.deps import require_auth
from app.main import app
from app.runtime.mcp import mcp_manager
from app.services import store


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

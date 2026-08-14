"""MCP connection manager: lifecycle, discovery, reconnect, and calls.

Uses an injected ``session_factory`` (fake session/stack) so no test ever
starts a subprocess or opens a network listener.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from app.runtime.mcp.manager import McpConnectionManager
from app.services import store


class FakeSession:
    def __init__(
        self,
        *,
        tools=None,
        resources=None,
        resource_templates=None,
        prompts=None,
        fail_init: bool = False,
        fail_discovery: bool = False,
    ) -> None:
        self.tools = tools or []
        self.resources = resources or []
        self.resource_templates = resource_templates or []
        self.prompts = prompts or []
        self.fail_init = fail_init
        self.fail_discovery = fail_discovery
        self.calls: list[tuple[str, dict]] = []
        self.fail_next_call = False

    async def initialize(self):
        if self.fail_init:
            raise RuntimeError("init failed")
        return SimpleNamespace(
            serverInfo=SimpleNamespace(name="fake", version="1.0"),
            capabilities=SimpleNamespace(),
        )

    async def list_tools(self, cursor=None):
        if self.fail_discovery:
            raise RuntimeError("discovery failed")
        return {"tools": self.tools, "nextCursor": None}

    async def list_resources(self, cursor=None):
        return {"resources": self.resources, "nextCursor": None}

    async def list_resource_templates(self, cursor=None):
        return {"resourceTemplates": self.resource_templates, "nextCursor": None}

    async def list_prompts(self, cursor=None):
        return {"prompts": self.prompts, "nextCursor": None}

    async def call_tool(self, name, arguments):
        if self.fail_next_call:
            self.fail_next_call = False
            raise ConnectionError("pipe closed")
        self.calls.append((name, arguments))
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=f"called {name}")]
        )

    async def read_resource(self, uri):
        return mcp_types.ReadResourceResult(
            contents=[
                mcp_types.TextResourceContents(uri=uri, text="body", mimeType="text/plain")
            ]
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


@pytest.fixture()
def manager(tmp_path) -> McpConnectionManager:
    store.rebind(tmp_path / "mcp.db")
    return McpConnectionManager()


def _factory_for(sessions: dict[str, FakeSession], created: list[str]):
    async def factory(server):
        created.append(server["id"])
        session = sessions[server["id"]]
        stack = AsyncExitStack()
        init_result = await session.initialize()
        return stack, session, init_result

    return factory


@pytest.mark.asyncio
async def test_connect_and_discover_persists_all_four_families(manager) -> None:
    store.create_mcp_server({"id": "s1", "name": "s1", "transport": "stdio", "command": "fake"})
    session = FakeSession(
        tools=[mcp_types.Tool(name="echo", inputSchema={"type": "object"})],
        resources=[mcp_types.Resource(name="r", uri="file:///r.txt")],
        resource_templates=[mcp_types.ResourceTemplate(name="t", uriTemplate="file:///{p}")],
        prompts=[mcp_types.Prompt(name="p")],
    )
    created: list[str] = []
    manager.session_factory = _factory_for({"s1": session}, created)

    result = await manager.connect_and_discover("s1")

    assert result["status"] == "connected"
    items = store.list_mcp_items("s1")
    kinds = {i["kind"] for i in items}
    assert kinds == {"tool", "resource", "resource_template", "prompt"}
    assert "s1" in manager.connected_server_ids()


@pytest.mark.asyncio
async def test_connect_and_discover_sets_error_on_init_failure(manager) -> None:
    store.create_mcp_server({"id": "s2", "name": "s2", "transport": "stdio", "command": "fake"})
    session = FakeSession(fail_init=True)
    manager.session_factory = _factory_for({"s2": session}, [])

    result = await manager.connect_and_discover("s2")

    assert result["status"] == "error"
    assert "s2" not in manager.connected_server_ids()


@pytest.mark.asyncio
async def test_connect_and_discover_keeps_old_items_on_partial_discovery_failure(manager) -> None:
    store.create_mcp_server({"id": "s3", "name": "s3", "transport": "stdio", "command": "fake"})
    good = FakeSession(tools=[mcp_types.Tool(name="echo", inputSchema={"type": "object"})])
    manager.session_factory = _factory_for({"s3": good}, [])
    await manager.connect_and_discover("s3")
    assert len(store.list_mcp_items("s3")) == 1

    # Force a reconnect that fails discovery — old snapshot must survive.
    await manager.close_server("s3")
    bad = FakeSession(fail_discovery=True)
    manager.session_factory = _factory_for({"s3": bad}, [])
    result = await manager.connect_and_discover("s3")

    assert result["status"] == "error"
    assert len(store.list_mcp_items("s3")) == 1


@pytest.mark.asyncio
async def test_second_connect_reuses_live_session(manager) -> None:
    store.create_mcp_server({"id": "s4", "name": "s4", "transport": "stdio", "command": "fake"})
    session = FakeSession()
    created: list[str] = []
    manager.session_factory = _factory_for({"s4": session}, created)

    await manager.connect_and_discover("s4")
    await manager.connect_and_discover("s4")

    assert created == ["s4"]  # factory called exactly once


@pytest.mark.asyncio
async def test_ensure_for_servers_skips_disabled(manager) -> None:
    store.create_mcp_server(
        {"id": "s5", "name": "s5", "transport": "stdio", "command": "fake", "enabled": False}
    )
    manager.session_factory = _factory_for({"s5": FakeSession()}, [])

    connected = await manager.ensure_for_servers({"s5"})

    assert connected == set()


@pytest.mark.asyncio
async def test_call_tool_dispatches_and_renders_text(manager) -> None:
    store.create_mcp_server({"id": "s6", "name": "s6", "transport": "stdio", "command": "fake"})
    session = FakeSession(tools=[mcp_types.Tool(name="echo", inputSchema={"type": "object"})])
    manager.session_factory = _factory_for({"s6": session}, [])
    await manager.connect_and_discover("s6")

    out = await manager.call_tool("mcp__s6__echo", {"x": 1})

    assert out == "called echo"
    assert session.calls == [("echo", {"x": 1})]


@pytest.mark.asyncio
async def test_call_tool_unknown_runtime_id_returns_error_string(manager) -> None:
    out = await manager.call_tool("mcp__missing__nope", {})
    assert out.startswith("Error:")


@pytest.mark.asyncio
async def test_call_tool_disabled_item_returns_error_string(manager) -> None:
    store.create_mcp_server({"id": "s7", "name": "s7", "transport": "stdio", "command": "fake"})
    session = FakeSession(tools=[mcp_types.Tool(name="echo", inputSchema={"type": "object"})])
    manager.session_factory = _factory_for({"s7": session}, [])
    await manager.connect_and_discover("s7")
    item = store.list_mcp_items("s7", kind="tool")[0]
    store.set_mcp_item_enabled(item["id"], False)

    out = await manager.call_tool("mcp__s7__echo", {})

    assert out.startswith("Error:")
    assert "disabled" in out.lower()


@pytest.mark.asyncio
async def test_read_resource_and_get_prompt(manager) -> None:
    store.create_mcp_server({"id": "s8", "name": "s8", "transport": "stdio", "command": "fake"})
    session = FakeSession()
    manager.session_factory = _factory_for({"s8": session}, [])
    await manager.connect_and_discover("s8")

    resource = await manager.read_resource("s8", "file:///r.txt")
    assert resource["contents"][0]["text"] == "body"

    prompt = await manager.get_prompt("s8", "p", {})
    assert prompt["messages"] == [{"role": "user", "text": "hi"}]


@pytest.mark.asyncio
async def test_dead_session_reconnects_once_and_calls_factory_again(manager) -> None:
    store.create_mcp_server({"id": "s9", "name": "s9", "transport": "stdio", "command": "fake"})
    session = FakeSession(tools=[mcp_types.Tool(name="echo", inputSchema={"type": "object"})])
    created: list[str] = []
    manager.session_factory = _factory_for({"s9": session}, created)
    await manager.connect_and_discover("s9")
    assert created == ["s9"]

    session.fail_next_call = True
    out = await manager.call_tool("mcp__s9__echo", {})

    assert out == "called echo"  # retried transparently
    assert created == ["s9", "s9"]  # factory invoked again to reconnect


@pytest.mark.asyncio
async def test_close_all_closes_every_session_once(manager) -> None:
    store.create_mcp_server({"id": "sa", "name": "sa", "transport": "stdio", "command": "fake"})
    store.create_mcp_server({"id": "sb", "name": "sb", "transport": "stdio", "command": "fake"})
    manager.session_factory = _factory_for(
        {"sa": FakeSession(), "sb": FakeSession()}, []
    )
    await manager.connect_and_discover("sa")
    await manager.connect_and_discover("sb")
    assert manager.connected_server_ids() == {"sa", "sb"}

    await manager.close_all()

    assert manager.connected_server_ids() == set()

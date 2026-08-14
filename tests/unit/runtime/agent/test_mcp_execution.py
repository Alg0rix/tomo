"""Agent execution dispatch: MCP calls go through the async manager path directly."""

from __future__ import annotations

import pytest

from app.runtime.agent.loop import _execute_authorized
from app.runtime.llm.base import ToolCall
from app.runtime.permissions.gate import Decision
from app.runtime.tools import registry


@pytest.mark.asyncio
async def test_execute_authorized_routes_mcp_call_without_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    async def fake_execute_async(name, arguments):
        seen["name"] = name
        seen["arguments"] = arguments
        return "mcp result"

    # Patch the exact symbol _execute_authorized calls (imported by name into
    # loop.py's module namespace) so a stray asyncio.to_thread(execute, ...)
    # regression would show up as the fake never being hit.
    monkeypatch.setattr(
        "app.runtime.agent.loop.execute_async", fake_execute_async
    )

    call = ToolCall(id="c1", name="mcp__github__create_issue", arguments={"title": "x"})
    decision = Decision(allowed=True, grant=None)

    result = await _execute_authorized(call, decision)

    assert result == "mcp result"
    assert seen == {"name": "mcp__github__create_issue", "arguments": {"title": "x"}}


@pytest.mark.asyncio
async def test_execute_authorized_still_runs_builtin_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})
    decision = Decision(allowed=True, grant=None)

    result = await _execute_authorized(call, decision)

    assert "hi" in result


@pytest.mark.asyncio
async def test_registry_execute_async_dispatches_to_mcp_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.runtime.mcp import mcp_manager

    async def fake_call_tool(runtime_id, arguments):
        return f"called {runtime_id} with {arguments}"

    monkeypatch.setattr(mcp_manager, "call_tool", fake_call_tool)

    out = await registry.execute_async("mcp__srv__tool", {"a": 1})

    assert out == "called mcp__srv__tool with {'a': 1}"

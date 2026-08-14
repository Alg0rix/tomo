"""Gated tool path emits approval_required (no full LLM hang risk)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.agent.loop import _run_one_gated_tool
from app.runtime.llm.base import ToolCall
from app.runtime.permissions import hitl
from app.runtime.permissions.modes import clear_session_modes, set_session_mode
from app.runtime.tools import sandbox
from app.services import store


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store.rebind(tmp_path / "perm-gate.db")
    clear_session_modes()
    hitl.clear_all_pending()
    monkeypatch.setenv("TOMO_WORK", str(tmp_path / "work"))
    yield
    clear_session_modes()
    hitl.clear_all_pending()
    sandbox.reset_agent()


@pytest.mark.asyncio
async def test_gated_bash_escape_approval() -> None:
    set_session_mode("sess_g", "manual")
    sandbox.bind_agent("main")
    call = ToolCall(
        id="c1",
        name="bash",
        arguments={"command": "ls ~/.tomo"},
    )
    events: list[dict] = []

    async def _consume() -> None:
        async for ev in _run_one_gated_tool(call, session_id="sess_g"):
            events.append(ev)
            if ev.get("kind") == "approval_required":
                hitl.resolve_approval(ev["id"], "deny")

    await _consume()
    assert any(e.get("kind") == "approval_required" for e in events)
    result = next(e for e in events if e.get("kind") == "tool_result")
    assert str(result.get("result", "")).startswith("BLOCKED")


@pytest.mark.asyncio
async def test_gated_mcp_tool_requires_approval_then_allows() -> None:
    """Every ``mcp__`` call is an external finding — gated even with no escape/dangerous match."""
    set_session_mode("sess_mcp", "manual")
    sandbox.bind_agent("main")
    call = ToolCall(
        id="c1",
        name="mcp__github__create_issue",
        arguments={"title": "x"},
    )
    events: list[dict] = []

    async def _consume() -> None:
        async for ev in _run_one_gated_tool(call, session_id="sess_mcp"):
            events.append(ev)
            if ev.get("kind") == "approval_required":
                hitl.resolve_approval(ev["id"], "once")

    await _consume()
    assert any(e.get("kind") == "approval_required" for e in events)
    result = next(e for e in events if e.get("kind") == "tool_result")
    # Approved, then dispatched — no live MCP server configured, so the
    # manager itself returns a bounded error string (not a permission block).
    assert not str(result.get("result", "")).startswith("BLOCKED")

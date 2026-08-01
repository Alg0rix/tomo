"""Subagent session inheritance and nested approval bypass."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from app.runtime.agent.loop import _authorize_tool
from app.runtime.agent.subagent import (
    bind_depth,
    drain_subagent_turn,
    reset_depth,
)
from app.runtime.llm.base import ToolCall
from app.runtime.permissions.gate import Decision, evaluate
from app.runtime.permissions.hitl import (
    await_approval,
    cancel_session_pending,
    clear_all_pending,
    create_approval,
)
from app.runtime.permissions.modes import (
    clear_session_modes,
    set_session_mode,
    toggle_auto,
)


@pytest.fixture(autouse=True)
def _clean_modes() -> None:
    clear_session_modes()
    clear_all_pending()
    yield
    clear_session_modes()
    clear_all_pending()
    reset_depth()


@pytest.mark.asyncio
async def test_drain_subagent_passes_session_id() -> None:
    seen: dict[str, Any] = {}

    async def fake_run_turn(
        _prompt: str,
        *,
        history=None,
        agent_id=None,
        session_id=None,
        llm=None,
        tools=None,
        **_kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        seen["agent_id"] = agent_id
        seen["session_id"] = session_id
        yield {"kind": "final", "content": "ok from ops"}

    events: list[dict[str, Any]] = []
    final = ""
    async for ev, out in drain_subagent_turn(
        "ops",
        from_agent_id="main",
        reason="check node",
        user_request="health check",
        session_id="sess_chat",
        run_turn_fn=fake_run_turn,
    ):
        events.append(ev)
        final = out

    assert seen["agent_id"] == "ops"
    assert seen["session_id"] == "sess_chat"
    assert final == "ok from ops"
    assert events[-1]["kind"] == "subagent_final"
    assert events[-1]["subagent"] is True


def test_evaluate_nested_auto_allows_escape(tmp_path: Path) -> None:
    set_session_mode("sess_chat", "manual")
    token = bind_depth(1)
    try:
        d = evaluate(
            "bash",
            {"command": "cat /etc/sysctl.conf"},
            work_root=tmp_path,
            session_id="sess_chat",
        )
    finally:
        reset_depth(token)
    assert d.allowed
    assert d.grant == "*"
    assert not d.needs_hitl


@pytest.mark.asyncio
async def test_nested_authorize_skips_hitl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime.tools.sandbox.resolve_work_root",
        lambda: tmp_path,
    )
    set_session_mode("sess_chat", "manual")
    call = ToolCall(
        id="c1",
        name="bash",
        arguments={"command": "cat /etc/sysctl.conf"},
    )

    token = bind_depth(1)
    try:
        items: list[Any] = []
        async for item in _authorize_tool(call, session_id="sess_chat"):
            items.append(item)
    finally:
        reset_depth(token)

    assert len(items) == 1
    decision = items[0]
    assert isinstance(decision, Decision)
    assert decision.allowed
    assert decision.grant == "*"
    assert not decision.needs_hitl


@pytest.mark.asyncio
async def test_top_level_authorize_still_needs_hitl_in_manual(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.runtime.tools.sandbox.resolve_work_root",
        lambda: tmp_path,
    )
    set_session_mode("sess_chat", "manual")
    call = ToolCall(
        id="c1",
        name="bash",
        arguments={"command": "cat /etc/sysctl.conf"},
    )

    reset_depth()

    async def _approve(_id: str):
        return "deny"

    monkeypatch.setattr(
        "app.runtime.permissions.hitl.await_approval",
        _approve,
    )
    monkeypatch.setattr(
        "app.runtime.permissions.hitl.create_approval",
        lambda **kwargs: {
            "kind": "approval_required",
            "id": "appr_1",
            **{k: v for k, v in kwargs.items() if k != "findings"},
            "findings": [],
        },
    )

    kinds: list[str] = []
    decision: Decision | None = None
    async for item in _authorize_tool(call, session_id="sess_chat"):
        if isinstance(item, Decision):
            decision = item
        else:
            kinds.append(item["kind"])

    assert "approval_required" in kinds
    assert decision is not None
    assert not decision.allowed


@pytest.mark.asyncio
async def test_toggle_auto_unsticks_pending_approval() -> None:
    payload = create_approval(
        tool="bash",
        args={"command": "ls ~"},
        findings=[],
        description="escape",
        session_id="sess_stuck",
    )

    async def _run() -> str:
        return await await_approval(payload["id"], timeout=2.0)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    on, notice = toggle_auto("sess_stuck")
    assert on is True
    assert "Cleared 1" in notice
    choice = await asyncio.wait_for(task, timeout=1.0)
    assert choice == "once"
    assert cancel_session_pending("sess_stuck") == 0

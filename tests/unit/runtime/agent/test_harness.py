"""Consolidated tests (merged from: test_mcp_execution.py, test_harness_improvements.py).
- test_mcp_execution.py: Agent execution dispatch: MCP calls go through the async manager path directly.
- test_harness_improvements.py: Harness reliability helpers: retry, compress, tool errors, ATG interfaces.
"""

from __future__ import annotations

import pytest
from app.runtime.agent.loop import _execute_authorized
from app.runtime.llm.base import ToolCall
from app.runtime.permissions.gate import Decision
from app.runtime.tools import registry
from app.runtime.agent.atg.interfaces import get_tool_interface
from app.runtime.agent.compress import maybe_compress_messages
from app.runtime.agent.retry import is_transient_llm_error, with_llm_retry
from app.runtime.agent.tool_errors import tool_result_is_error
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.agent.loop import run_turn
from tests.fakes.llm import ScriptedLLM, text_reply


# --- from test_mcp_execution.py ---
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


# --- from test_harness_improvements.py ---
def test_tool_result_empty_is_not_error_by_default() -> None:
    assert tool_result_is_error("") is False
    assert tool_result_is_error("   ") is False
    assert tool_result_is_error("Error: boom") is True
    assert tool_result_is_error("BLOCKED: no") is True
    assert tool_result_is_error("ok\nexit code: 0") is False
    assert tool_result_is_error("fail\nexit code: 2") is True


def test_atg_interfaces_use_result_key_only() -> None:
    for name in ("read_file", "bash", "web_fetch", "web_search", "recall", "ghost"):
        outs = get_tool_interface(name)["outputs"]
        assert list(outs.keys()) == ["result"]


def test_transient_classifier() -> None:
    assert is_transient_llm_error(TimeoutError("timed out"))
    assert is_transient_llm_error(RuntimeError("LLM returned HTTP 429: slow down"))
    assert is_transient_llm_error(
        RuntimeError(
            "LLM request failed: empty choices[] — provider returned no completion"
        )
    )
    assert is_transient_llm_error(
        RuntimeError("stream ended with no content and no tool calls")
    )
    assert not is_transient_llm_error(RuntimeError("LLM returned HTTP 401: bad key"))


@pytest.mark.asyncio
async def test_with_llm_retry_retries_once() -> None:
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timeout")
        return "ok"

    assert await with_llm_retry(flaky, base_delay_s=0.01) == "ok"
    assert calls["n"] == 2


def test_compress_collapses_old_tool_exchanges() -> None:
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": "x" * 800,
            }
        )
    msgs.append({"role": "user", "content": "latest question"})
    out = maybe_compress_messages(msgs, soft_limit_tokens=500, keep_recent=4)
    assert out[0]["role"] == "system"
    assert any(
        m.get("role") == "user" and "compressed" in str(m.get("content", "")).lower()
        for m in out
    )
    assert len(out) < len(msgs)


@pytest.mark.asyncio
async def test_parallel_readonly_tools_in_one_round(monkeypatch) -> None:
    """Two read_file calls in one round should both execute (order preserved)."""
    calls: list[str] = []

    def _exec(name, args):
        calls.append(args.get("path") or name)
        return f"content:{args.get('path')}"

    monkeypatch.setattr("app.runtime.tools.registry.execute", _exec)
    # Bypass permission gate evaluate → always allow.
    from app.runtime.permissions.gate import Decision

    monkeypatch.setattr(
        "app.runtime.agent.loop.evaluate",
        lambda *a, **k: Decision(allowed=True),
    )

    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="read_file",
                        arguments={"path": "a.py"},
                    ),
                    ToolCall(
                        id="c2",
                        name="read_file",
                        arguments={"path": "b.py"},
                    ),
                ],
            ),
            text_reply("done"),
        ]
    )
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    events = [ev async for ev in run_turn("read both", llm=llm, tools=tools)]
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 2
    assert results[0]["result"] == "content:a.py"
    assert results[1]["result"] == "content:b.py"
    assert set(calls) == {"a.py", "b.py"}
    final = next(e for e in events if e["kind"] == "final")
    assert final.get("metrics", {}).get("parallel_tool_peak", 0) >= 2



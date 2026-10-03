"""Consolidated tests (merged from: test_mcp_execution.py, test_harness_improvements.py).
- test_mcp_execution.py: Agent execution dispatch: MCP calls go through the async manager path directly.
- test_harness_improvements.py: Harness reliability helpers: retry, compress, tool errors, ATG interfaces.
"""

from __future__ import annotations

import pytest
from app.runtime.agent.loop import _execute_authorized
from app.runtime.llm.base import ToolCall
from app.runtime.permissions.gate import Decision
from app.runtime.agent.atg.interfaces import get_tool_interface
from app.runtime.agent.compress import maybe_compress_messages
from app.runtime.agent.tool_errors import tool_result_is_error
from app.runtime.llm.base import LLMResponse
from app.runtime.agent.loop import run_turn
from tests.fakes.llm import ScriptedLLM, text_reply


# --- from test_mcp_execution.py ---


@pytest.mark.asyncio
async def test_execute_authorized_still_runs_builtin_tools(
    tmp_path,
) -> None:
    from app.runtime.tools.sandbox import bind_agent, reset_agent
    from app.runtime.tools.workplace_ctx import bind_workplace, reset_workplace
    from app.services import store

    # This tests real local dispatch, independent of earlier agents/workplaces.
    store.rebind(tmp_path / "builtin-tools.db")
    agent_token = bind_agent(None)
    workplace_tokens = bind_workplace(force_work_dir=True)
    call = ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})
    decision = Decision(allowed=True, grant=None)

    try:
        result = await _execute_authorized(call, decision)
        assert "hi" in result
    finally:
        reset_workplace(workplace_tokens)
        reset_agent(agent_token)




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



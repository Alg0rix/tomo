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
    from app.services import store

    # This tests real local dispatch, independent of earlier agents/workplaces.
    from tests.fakes.access import owned_admin_scope
    store.rebind(tmp_path / "builtin-tools.db")
    call = ToolCall(id="c1", name="bash", arguments={"command": "echo hi"})
    decision = Decision(allowed=True, grant=None)

    with owned_admin_scope():
        result = await _execute_authorized(call, decision)
        assert "hi" in result




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
async def test_parallel_readonly_tools_in_one_round(tmp_path) -> None:
    """Existing independent files execute concurrently, with correctly paired results."""
    from tests.fakes.access import owned_admin_scope

    scope = owned_admin_scope()
    ctx, root = scope.__enter__()
    sid = ctx.session_id
    for name in ("a.py", "b.py"):
        (root / name).write_text(f"content:{name}")

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
    try:
        events = [ev async for ev in run_turn("read both", llm=llm, tools=tools, session_id=sid, agent_id="main")]
    finally:
        scope.__exit__(None, None, None)
    results = {e["call_id"]: e for e in events if e["kind"] == "tool_result"}
    assert len(results) == 2
    assert not any(e["error"] for e in results.values())
    assert "content:a.py" in results["c1"]["result"]
    assert "content:b.py" in results["c2"]["result"]
    final = next(e for e in events if e["kind"] == "final")
    assert final.get("metrics", {}).get("parallel_tool_peak", 0) >= 2



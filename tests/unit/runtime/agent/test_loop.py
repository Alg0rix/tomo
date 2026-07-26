"""Agent turn loop tests with mock LLMs.

Validates the internal event-stream contract the chat layer (Task 6) maps
onto SSE:

* text-only prompt -> a single ``final`` event;
* a calculator prompt -> ``tool`` -> ``tool_result`` -> ``final``;
* an ever-tool-calling mock -> an ``error`` event at the iteration cap;
* reasoning text alongside a tool call -> a leading ``thinking`` event;
* an LLM backend failure -> an ``error`` event.

The loop is exercised with the real :class:`MockLLMClient` for the happy
paths and small stub clients for the adversarial paths, so the contract is
verified end to end without any network or HTTP.
"""

from __future__ import annotations

from typing import Any

from app.runtime.agent.loop import run_turn
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.mock import _CALC_FINAL, _DEFAULT_REPLY


def _calc_tools() -> list[dict[str, Any]]:
    """Minimal OpenAI tool schema advertising the calculator."""
    return [{"type": "function", "function": {"name": "calculator"}}]


async def _collect(user_message: str, **kw: Any) -> list[dict[str, Any]]:
    """Drain the ``run_turn`` async generator into a list of events."""
    return [ev async for ev in run_turn(user_message, **kw)]


# --- happy paths --------------------------------------------------------


async def test_text_only_path_yields_single_final() -> None:
    events = await _collect("hello", llm=MockLLMClient(), tools=_calc_tools())
    assert [e["kind"] for e in events] == ["final"]
    assert events[0]["content"] == _DEFAULT_REPLY


async def test_calculator_path_emits_tool_then_result_then_final() -> None:
    events = await _collect("calculate 2 + 2", llm=MockLLMClient(), tools=_calc_tools())
    assert [e["kind"] for e in events] == ["tool", "tool_result", "final"]

    tool_ev, result_ev, final_ev = events
    assert tool_ev == {
        "kind": "tool",
        "tool": "calculator",
        "args": {"expression": "2 + 2"},
    }
    assert result_ev["tool"] == "calculator"
    assert result_ev["result"] == "4"
    assert result_ev["error"] is False
    assert final_ev == {"kind": "final", "content": _CALC_FINAL}


async def test_calculator_error_result_sets_error_flag() -> None:
    """A tool that returns an ``Error:`` string must flag ``error=True``."""
    events = await _collect("calculate 1 / 0 =", llm=MockLLMClient(), tools=_calc_tools())
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert result_ev["error"] is True
    assert result_ev["result"].startswith("Error")


async def test_history_rebuilt_so_new_calc_turn_still_calls_tool() -> None:
    """A prior completed calc turn in history must not suppress a fresh one."""
    history = [
        {"type": "user", "content": "calculate 7 - 3"},
        {"type": "tool_call", "function": "calculator", "params": {"expression": "7 - 3"}},
        {"type": "tool_output", "content": "4"},
        {"type": "final", "content": _CALC_FINAL},
    ]
    events = await _collect(
        "calculate 5 + 5", llm=MockLLMClient(), tools=_calc_tools(), history=history
    )
    assert [e["kind"] for e in events] == ["tool", "tool_result", "final"]
    assert events[0]["args"] == {"expression": "5 + 5"}
    assert events[1]["result"] == "10"


# --- adversarial paths --------------------------------------------------


async def test_max_iterations_stops_cleanly_with_error_event() -> None:
    """A mock that always requests tools must stop at the cap with ``error``."""
    events = await _collect(
        "keep calling tools",
        llm=_AlwaysToolMock(),
        tools=_calc_tools(),
        max_iterations=2,
    )
    # Two rounds of (tool, tool_result), then the budget-exhausted error.
    assert [e["kind"] for e in events] == [
        "tool",
        "tool_result",
        "tool",
        "tool_result",
        "error",
    ]
    assert "max tool iterations" in events[-1]["message"]
    assert "2" in events[-1]["message"]


async def test_thinking_emitted_when_content_accompanies_tool_calls() -> None:
    events = await _collect(
        "plan then calc", llm=_ThinkingThenFinalMock(), tools=_calc_tools()
    )
    assert [e["kind"] for e in events] == ["thinking", "tool", "tool_result", "final"]
    assert events[0] == {"kind": "thinking", "content": "Let me compute that."}
    assert events[1]["args"] == {"expression": "2 + 2"}
    assert events[3] == {"kind": "final", "content": "Done: 4"}


async def test_llm_exception_surfaces_as_error_event() -> None:
    events = await _collect("boom", llm=_BoomMock(), tools=_calc_tools())
    assert [e["kind"] for e in events] == ["error"]
    assert "LLM request failed" in events[0]["message"]
    assert "upstream blew up" in events[0]["message"]


async def test_empty_tool_list_keeps_text_only_path() -> None:
    """No tools advertised -> mock returns its default reply as ``final``."""
    events = await _collect("calculate 2 + 2", llm=MockLLMClient(), tools=[])
    assert [e["kind"] for e in events] == ["final"]
    assert events[0]["content"] == _DEFAULT_REPLY


# --- stub LLM clients ---------------------------------------------------


class _AlwaysToolMock:
    """Always returns a calculator tool call — never converges."""

    async def complete(self, messages, tools=None):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_loop",
                    name="calculator",
                    arguments={"expression": "1 + 1"},
                )
            ],
        )


class _ThinkingThenFinalMock:
    """First call: reasoning text + a calculator call; then final text.

    Instance-level state (not class-level) so each test gets a fresh mock.
    """

    def __init__(self) -> None:
        self._seen = False

    async def complete(self, messages, tools=None):
        if not self._seen:
            self._seen = True
            return LLMResponse(
                content="Let me compute that.",
                tool_calls=[
                    ToolCall(
                        id="call_think",
                        name="calculator",
                        arguments={"expression": "2 + 2"},
                    )
                ],
            )
        return LLMResponse(content="Done: 4", tool_calls=[])


class _BoomMock:
    """Simulates an upstream LLM failure (e.g. ``LLMRequestError``)."""

    async def complete(self, messages, tools=None):
        raise RuntimeError("upstream blew up")

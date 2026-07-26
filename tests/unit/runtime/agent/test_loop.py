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
from app.runtime.llm import LLMConfigError
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.mock import _CALC_FINAL, _DEFAULT_REPLY, _RECALL_FINAL
from app.services import store


def _calc_tools() -> list[dict[str, Any]]:
    """Minimal OpenAI tool schema advertising the calculator."""
    return [{"type": "function", "function": {"name": "calculator"}}]


def _recall_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "recall"}}]


async def _collect(user_message: str | None, **kw: Any) -> list[dict[str, Any]]:
    """Drain the ``run_turn`` async generator into a list of events."""
    return [ev async for ev in run_turn(user_message, **kw)]


def _kinds(events: list[dict[str, Any]], *, drop_delta: bool = False) -> list[str]:
    kinds = [e["kind"] for e in events]
    if drop_delta:
        return [k for k in kinds if k != "delta"]
    return kinds


def _final(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in reversed(events) if e["kind"] == "final")


# --- happy paths --------------------------------------------------------


async def test_text_only_path_yields_single_final() -> None:
    events = await _collect("hello", llm=MockLLMClient(), tools=_calc_tools())
    assert _kinds(events, drop_delta=True) == ["final"]
    assert _final(events)["content"] == _DEFAULT_REPLY
    assert "".join(e["content"] for e in events if e["kind"] == "delta") == _DEFAULT_REPLY


async def test_calculator_path_emits_tool_then_result_then_final() -> None:
    events = await _collect("calculate 2 + 2", llm=MockLLMClient(), tools=_calc_tools())
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]

    tool_ev = next(e for e in events if e["kind"] == "tool")
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    final_ev = _final(events)
    assert tool_ev == {
        "kind": "tool",
        "tool": "calculator",
        "args": {"expression": "2 + 2"},
    }
    assert result_ev["tool"] == "calculator"
    assert result_ev["result"] == "4"
    assert result_ev["error"] is False
    assert final_ev["content"] == _CALC_FINAL
    assert final_ev.get("already_streamed") is True


async def test_recall_path_returns_seeded_fact(tmp_path) -> None:
    """New session path: MockLLM calls recall; result includes seeded KB fact."""
    store.rebind(tmp_path / "recall_loop.db")
    events = await _collect(
        "What is the Q3 vendor onboarding deadline?",
        llm=MockLLMClient(),
        tools=_recall_tools(),
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    tool_ev = next(e for e in events if e["kind"] == "tool")
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert tool_ev["tool"] == "recall"
    assert "vendor" in tool_ev["args"]["query"].lower() or "deadline" in tool_ev["args"]["query"].lower()
    assert "October 15, 2026" in result_ev["result"]
    assert result_ev["error"] is False
    assert _final(events)["content"] == _RECALL_FINAL


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
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    assert next(e for e in events if e["kind"] == "tool")["args"] == {"expression": "5 + 5"}
    assert next(e for e in events if e["kind"] == "tool_result")["result"] == "10"


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
    assert _kinds(events, drop_delta=True) == ["thinking", "tool", "tool_result", "final"]
    assert events[0] == {"kind": "thinking", "content": "Let me compute that."}
    assert next(e for e in events if e["kind"] == "tool")["args"] == {"expression": "2 + 2"}
    assert _final(events)["content"] == "Done: 4"


async def test_llm_exception_surfaces_as_error_event() -> None:
    events = await _collect("boom", llm=_BoomMock(), tools=_calc_tools())
    assert [e["kind"] for e in events] == ["error"]
    assert "LLM request failed" in events[0]["message"]
    assert "upstream blew up" in events[0]["message"]


async def test_empty_tool_list_keeps_text_only_path() -> None:
    """No tools advertised -> mock returns its default reply as ``final``."""
    events = await _collect("calculate 2 + 2", llm=MockLLMClient(), tools=[])
    assert _kinds(events, drop_delta=True) == ["final"]
    assert _final(events)["content"] == _DEFAULT_REPLY


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


# --- turn-scoped ids / setup errors / user_message=None ----------------


class _RecordingMock:
    """Returns the default final reply; records messages passed to each call."""

    def __init__(self) -> None:
        self.captured: list[list[dict[str, Any]]] = []

    async def complete(self, messages, tools=None):
        self.captured.append(list(messages))
        return LLMResponse(content=_DEFAULT_REPLY, tool_calls=[])


class _EmptyIdTwoRoundMock:
    """Calculator tool calls with empty ids for two rounds, then a final answer.

    Records the messages handed to each ``complete`` call so the test can
    assert synthesised ids stay distinct across rounds.
    """

    def __init__(self) -> None:
        self._round = 0
        self.captured: list[list[dict[str, Any]]] = []

    async def complete(self, messages, tools=None):
        self.captured.append(list(messages))
        if self._round < 2:
            self._round += 1
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="",
                        name="calculator",
                        arguments={"expression": "1 + 1"},
                    )
                ],
            )
        return LLMResponse(content="done", tool_calls=[])


async def test_empty_ids_stay_distinct_across_two_rounds() -> None:
    """Empty tool-call ids must not collide between completion rounds."""
    mock = _EmptyIdTwoRoundMock()
    events = await _collect(
        "calc twice", llm=mock, tools=_calc_tools(), max_iterations=4
    )
    assert _kinds(events, drop_delta=True) == [
        "tool",
        "tool_result",
        "tool",
        "tool_result",
        "final",
    ]
    # Messages fed to the final complete carry both rounds' tool ids.
    final_messages = mock.captured[-1]
    assistant_ids = [
        tc["id"]
        for m in final_messages
        if m.get("role") == "assistant" and m.get("tool_calls")
        for tc in m["tool_calls"]
    ]
    tool_ids = [m["tool_call_id"] for m in final_messages if m.get("role") == "tool"]
    assert len(assistant_ids) == 2
    assert len(tool_ids) == 2
    # Distinct across rounds (the old ``call_{i}`` per-response scheme collided).
    assert assistant_ids[0] != assistant_ids[1]
    assert tool_ids[0] != tool_ids[1]
    # Each assistant id matches its tool result id, in order.
    assert assistant_ids == tool_ids


async def test_setup_failure_surfaces_as_error_event(monkeypatch) -> None:
    """A failing ``get_llm`` yields an error event; ``run_turn`` never raises."""
    def _boom(agent_id=None) -> None:
        raise LLMConfigError("bad provider config")

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _boom)
    events = await _collect("hi", tools=_calc_tools())
    assert [e["kind"] for e in events] == ["error"]
    assert "setup" in events[0]["message"].lower()
    assert "bad provider config" in events[0]["message"]


async def test_get_openai_tools_failure_surfaces_as_error_event(monkeypatch) -> None:
    """A failing ``get_openai_tools`` during setup yields an error event."""
    def _boom() -> None:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr("app.runtime.agent.loop.get_openai_tools", _boom)
    events = await _collect("hi", llm=MockLLMClient())
    assert [e["kind"] for e in events] == ["error"]
    assert "setup" in events[0]["message"].lower()
    assert "registry exploded" in events[0]["message"]


async def test_user_message_none_does_not_duplicate_history_user() -> None:
    """``user_message=None`` must not append a second trailing user message."""
    history = [{"type": "user", "content": "the new question"}]
    recorder = _RecordingMock()
    events = await _collect(None, llm=recorder, tools=_calc_tools(), history=history)
    assert _kinds(events, drop_delta=True) == ["final"]
    msgs = recorder.captured[-1]
    users = [m for m in msgs if m["role"] == "user"]
    assert users == [{"role": "user", "content": "the new question"}]


async def test_error_flag_requires_error_colon_prefix(monkeypatch) -> None:
    """A result starting with ``Error`` but not ``Error:`` is not an error."""
    monkeypatch.setattr(
        "app.runtime.agent.loop.execute",
        lambda name, args: "Errorless computation succeeded",
    )
    events = await _collect("calculate 2 + 2", llm=MockLLMClient(), tools=_calc_tools())
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert result_ev["error"] is False
    assert result_ev["result"] == "Errorless computation succeeded"


# --- swarm delegation ---------------------------------------------------


def _delegate_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "delegate"}}]


class _DelegateMock:
    """Coordinator calls ``delegate`` once; never produces its own final."""

    async def complete(self, messages, tools=None):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_delegate",
                    name="delegate",
                    arguments={"agent_id": "ops", "reason": "ops task"},
                )
            ],
        )


async def test_successful_delegate_yields_delegate_event_and_stops(
    monkeypatch,
) -> None:
    """After a successful delegate tool, loop emits ``delegate`` and returns."""
    monkeypatch.setattr(
        "app.runtime.agent.loop.execute",
        lambda name, args: "Delegated to ops",
    )
    events = await _collect(
        "ask ops to help",
        llm=_DelegateMock(),
        tools=_delegate_tools(),
        agent_id="main",
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "delegate"]
    handoff = next(e for e in events if e["kind"] == "delegate")
    assert handoff["from"] == "main"
    assert handoff["to"] == "ops"
    assert handoff["reason"] == "ops task"
    assert not any(e["kind"] == "final" for e in events)


async def test_failed_delegate_continues_tool_loop(monkeypatch) -> None:
    """A rejected delegate is a normal tool error; loop keeps iterating."""

    class _DelegateThenFinal:
        def __init__(self) -> None:
            self._n = 0

        async def complete(self, messages, tools=None):
            self._n += 1
            if self._n == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_bad",
                            name="delegate",
                            arguments={"agent_id": "ghost"},
                        )
                    ],
                )
            return LLMResponse(content="I'll handle it myself.", tool_calls=[])

    monkeypatch.setattr(
        "app.runtime.agent.loop.execute",
        lambda name, args: "Error: 'ghost' is not a member of this session",
    )
    events = await _collect(
        "delegate to ghost",
        llm=_DelegateThenFinal(),
        tools=_delegate_tools(),
        agent_id="main",
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    assert not any(e["kind"] == "delegate" for e in events)
    assert _final(events)["content"] == "I'll handle it myself."

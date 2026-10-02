"""Agent turn loop tests with ScriptedLLM and SQLite session history.

Validates the internal event-stream contract the chat layer maps onto SSE:

* text-only prompt -> a single ``final`` event;
* a scripted bash tool call -> ``tool`` -> ``tool_result`` -> ``final``;
* an ever-tool-calling stub -> an ``error`` event at the iteration cap;
* reasoning text alongside a tool call -> a leading ``thinking`` event;
* an LLM backend failure -> an ``error`` event.

Happy paths use :class:`~tests.fakes.llm.ScriptedLLM` with explicit response
queues. Prior-turn context is loaded from SQLite via ``append_session_history``
/ ``get_session_history`` — not hand-built lists.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from app.runtime.agent.loop import _truncate_result, run_turn
from app.runtime.agent.compress import estimate_prompt_tokens, prompt_budget
from app.runtime.llm import LLMConfigError
from app.runtime.llm.base import LLMResponse, ToolCall
from app.services import store
from tests.fakes.llm import ScriptedLLM, bash_call, memory_search_call, text_reply, tool_then_text

_DEFAULT_REPLY = "Ready to help."
_BASH_FINAL = "The command finished."
_RECALL_FINAL = "I found the relevant knowledge base entry."


class ContextCapturingLLM(ScriptedLLM):
    context_window = 128_000

    def __init__(self, *, failures=0):
        super().__init__([text_reply("Fits now.")])
        self.failures = failures
        self.prompts = []

    async def complete(self, messages, tools=None):
        self.prompts.append([dict(message) for message in messages])
        if self.failures:
            self.failures -= 1
            raise RuntimeError("context_length_exceeded: prompt is too long")
        return await super().complete(messages, tools)


async def test_500k_history_compacts_before_first_request():
    llm = ContextCapturingLLM()
    history = [{"type": "user", "content": "X" * 2_000_000},
               {"type": "final", "content": "Earlier response"}]
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions", history=history)
    assert _final(events)["metrics"]["compressed"] is True
    assert any("Compacted" in event.get("message", "") for event in events)
    assert len(llm.prompts) == 1
    assert estimate_prompt_tokens(llm.prompts[0]) <= prompt_budget(llm.context_window)
    assert any(message.get("content") == "Continue." for message in llm.prompts[0])
    assert len(history[0]["content"]) == 2_000_000


async def test_oversized_latest_request_never_reaches_provider():
    llm = ContextCapturingLLM()
    events = await _collect("X" * 2_000_000, llm=llm, tools=[], system_prompt="Instructions")
    assert _kinds(events) == ["error"]
    assert "prompt budget" in events[0]["message"]
    assert llm.prompts == []


async def test_context_rejection_compacts_and_retries_once():
    llm = ContextCapturingLLM(failures=1)
    history = [{"type": "user", "content": "X" * 100_000},
               {"type": "final", "content": "Old response"}]
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions", history=history)
    assert _final(events)["content"] == "Fits now."
    assert _final(events)["metrics"]["llm_retries"] == 1
    assert len(llm.prompts) == 2
    assert estimate_prompt_tokens(llm.prompts[1]) < estimate_prompt_tokens(llm.prompts[0])
    assert any("compacting conversation and retrying" in event.get("message", "") for event in events)


async def test_repeated_context_rejection_stops_after_one_retry():
    llm = ContextCapturingLLM(failures=2)
    history = [{"type": "user", "content": "X" * 100_000},
               {"type": "final", "content": "Old response"}]
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions", history=history)
    assert events[-1]["kind"] == "error"
    assert len(llm.prompts) == 2


async def test_context_error_after_streaming_is_not_replayed():
    class PartialLLM(ContextCapturingLLM):
        async def stream_complete(self, messages, tools=None):
            self.prompts.append(messages)
            yield {"type": "delta", "content": "Started"}
            raise RuntimeError("context_length_exceeded")

    llm = PartialLLM()
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions")
    assert len(llm.prompts) == 1
    assert _kinds(events) == ["delta", "error"]


async def test_context_resolution_uses_frozen_client_profile(monkeypatch):
    llm = ContextCapturingLLM()
    llm.context_window = None
    llm.context_profile = {"model": "actual-running-model", "base_url": "https://actual.test/v1"}
    seen = []

    async def resolve(agent_id, *, session_id, profile):
        seen.append(profile)
        return 128_000

    monkeypatch.setattr("app.runtime.llm.context_window.resolve_context_window", resolve)
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions")
    assert _final(events)["content"] == "Fits now."
    assert seen == [llm.context_profile]


async def test_minimal_slotted_llm_client_remains_supported():
    class SlottedLLM:
        __slots__ = ()

        async def complete(self, messages, tools=None):
            return text_reply("Works.")

    events = await _collect("Hello", llm=SlottedLLM(), tools=[], system_prompt="Instructions")
    assert _final(events)["content"] == "Works."


def test_tool_result_caps_are_tool_appropriate() -> None:
    catalog = "skill-1: useful skill\n" * 300
    assert _truncate_result(catalog, tool_name="list_skills") == catalog

    paginated = "header\n" + ("content\n" * 4000) + "Continue with offset=42."
    shortened = _truncate_result(paginated, tool_name="read_file")
    assert len(shortened) < len(paginated)
    assert "Continue with offset=42." in shortened
    assert "Continue with Continue" not in shortened
    assert "truncated" not in shortened

    bash_output = "output\n" * 1000
    assert _truncate_result(bash_output, tool_name="bash") == bash_output


def _bash_tools() -> list[dict[str, Any]]:
    """Minimal OpenAI tool schema advertising bash."""
    return [{"type": "function", "function": {"name": "bash"}}]


def _memory_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "memory"}}]


async def _collect(user_message: str | None, **kw: Any) -> list[dict[str, Any]]:
    """Drain the ``run_turn`` async generator into a list of events."""
    # Unit tests use scripted LLMs — keep ATG off unless a test opts in.
    kw.setdefault("enable_atg", False)
    return [ev async for ev in run_turn(user_message, **kw)]


def _kinds(events: list[dict[str, Any]], *, drop_delta: bool = False) -> list[str]:
    kinds = [e["kind"] for e in events]
    if drop_delta:
        return [k for k in kinds if k not in {"delta", "tool_output_delta"}]
    return kinds


def _final(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in reversed(events) if e["kind"] == "final")


def _session_history(tmp_path, *entries: dict[str, Any], db_name: str = "loop.db") -> list[dict[str, Any]]:
    """Persist entries in a fresh SQLite session and return store history."""
    store.rebind(tmp_path / db_name)
    sid = store.create_swarm_session(["main"], user_id="web")
    for entry in entries:
        store.append_session_history(sid, entry)
    return store.get_session_history(sid)


# --- happy paths --------------------------------------------------------


async def test_text_only_path_yields_single_final() -> None:
    llm = ScriptedLLM([text_reply(_DEFAULT_REPLY)])
    events = await _collect("hello", llm=llm, tools=_bash_tools())
    assert _kinds(events, drop_delta=True) == ["final"]
    assert _final(events)["content"] == _DEFAULT_REPLY
    assert "".join(e["content"] for e in events if e["kind"] == "delta") == _DEFAULT_REPLY


async def test_loop_keeps_live_context_after_history(monkeypatch) -> None:
    from app.runtime.agent import context

    seen = []

    class CapturingLLM(ScriptedLLM):
        async def complete(self, messages, tools=None):
            seen.extend(messages)
            return await super().complete(messages, tools)

    monkeypatch.setattr(context, "build_live_context", lambda *a, **kw: "host is online now")
    monkeypatch.setattr("app.runtime.memory.retrieve.retrieve_for_turn", lambda *a, **kw: "")
    await _collect("next", llm=CapturingLLM([text_reply("ok")]), tools=[],
                   history=[{"type": "user", "content": "earlier"}])
    assert seen[1] == {"role": "user", "content": "earlier"}
    assert "host is online now" not in seen[0]["content"]
    assert "host is online now" in seen[2]["content"]


async def test_session_reasoning_effort_reaches_llm_factory(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "reasoning-loop.db")
    store.create_llm_profile(
        {
            "id": "default",
            "name": "D",
            "api_key": "sk-d",
            "model": "model-a",
            "reasoning_efforts": ["balanced", "deep"],
        }
    )
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"], user_id="web")
    store.set_session_reasoning_effort(sid, "balanced")
    seen: dict[str, Any] = {}

    def _get_llm(agent_id=None, reasoning_effort=None):
        seen["agent_id"] = agent_id
        seen["reasoning_effort"] = reasoning_effort
        return ScriptedLLM([text_reply("ok")])

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _get_llm)
    events = await _collect("hello", session_id=sid, agent_id="main", tools=[])

    assert _final(events)["content"] == "ok"
    assert seen["agent_id"] == "main"
    assert seen["reasoning_effort"] == "balanced"


async def test_metrics_accumulate_provider_usage_across_rounds() -> None:
    """Final metrics sum prompt/completion tokens from every LLM round."""
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id="c1", name="bash", arguments={"command": "echo 1"})
                ],
                prompt_tokens=100,
                completion_tokens=10,
            ),
            LLMResponse(
                content=_BASH_FINAL,
                tool_calls=[],
                prompt_tokens=150,
                completion_tokens=20,
            ),
        ]
    )
    events = await _collect("run: echo 1", llm=llm, tools=_bash_tools())
    metrics = _final(events).get("metrics") or {}
    assert metrics.get("prompt_tokens") == 250
    assert metrics.get("completion_tokens") == 30
    assert metrics.get("tokens") == 280
    assert metrics.get("llm_rounds") == 2


async def test_metrics_estimate_usage_when_provider_omits() -> None:
    """When LLMResponse has 0 usage, loop estimates in/out from messages."""
    llm = ScriptedLLM([text_reply("ok")])
    events = await _collect("hello world from usage test", llm=llm, tools=[])
    metrics = _final(events).get("metrics") or {}
    assert int(metrics.get("prompt_tokens") or 0) > 0
    assert int(metrics.get("completion_tokens") or 0) > 0


async def test_deltas_stream_as_produced_not_buffered_until_round_end() -> None:
    """Regression: the LLM round must forward each delta as it arrives.

    ``_llm_round_with_retry`` used to fully drain the underlying
    ``stream_complete`` generator into a list before yielding anything, which
    turns real provider token-streaming into one big burst at the very end
    (long "typing" wait, then the whole reply appears at once). The consumer
    must see the first delta while the producer has only made the first of
    several chunks available — not after all of them.
    """
    produced = {"n": 0}
    total_chunks = 5

    class _StreamingLLM:
        async def complete(self, messages, tools=None):
            raise AssertionError("stream_complete is available — must not fall back")

        async def stream_complete(self, messages, tools=None):
            for i in range(total_chunks):
                produced["n"] = i + 1
                yield {"type": "delta", "content": f"chunk{i} "}
            yield {"type": "done", "response": text_reply("chunk0 chunk1 chunk2 chunk3 chunk4 ")}

    first_delta_producer_count = None
    async for ev in run_turn(
        "hello", llm=_StreamingLLM(), tools=[], enable_atg=False
    ):
        if ev["kind"] == "delta" and first_delta_producer_count is None:
            first_delta_producer_count = produced["n"]
            break

    assert first_delta_producer_count == 1, (
        "first delta reached the consumer after producing "
        f"{first_delta_producer_count}/{total_chunks} chunks — the round is "
        "buffered instead of streamed"
    )


async def test_hung_builtin_returns_tool_error_and_turn_finishes(monkeypatch) -> None:
    from app.runtime.agent import loop
    from app.runtime.tools import registry

    release = threading.Event()
    monkeypatch.setattr(loop, "_TOOL_TIMEOUT", 0.02)
    monkeypatch.setitem(registry._BACKENDS, "read_file", lambda args: (release.wait(1), "late")[1])
    llm = ScriptedLLM(tool_then_text(
        LLMResponse(content=None, tool_calls=[ToolCall(id="r1", name="read_file", arguments={"path": "x"})]),
        "handled timeout",
    ))
    try:
        events = await _collect("read", llm=llm, tools=[{"type": "function", "function": {"name": "read_file"}}])
        result = next(e for e in events if e["kind"] == "tool_result")
        assert result["error"] is True
        assert "timed out" in result["result"]
        assert _final(events)["content"] == "handled timeout"
    finally:
        release.set()


async def test_bash_path_emits_tool_then_result_then_final() -> None:
    llm = ScriptedLLM(tool_then_text(bash_call("echo 4"), _BASH_FINAL))
    events = await _collect("run: echo 4", llm=llm, tools=_bash_tools())
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]

    tool_ev = next(e for e in events if e["kind"] == "tool")
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    final_ev = _final(events)
    assert tool_ev["kind"] == "tool"
    assert tool_ev["tool"] == "bash"
    assert tool_ev["args"] == {"command": "echo 4"}
    assert tool_ev.get("call_id")
    assert result_ev["tool"] == "bash"
    assert result_ev["result"].strip() == "4"
    assert result_ev["error"] is False
    live = [e for e in events if e["kind"] == "tool_output_delta"]
    assert "".join(e["content"] for e in live) == "4\n"
    assert all(e["call_id"] == tool_ev["call_id"] for e in live)
    assert events.index(live[-1]) < events.index(result_ev)
    assert result_ev.get("call_id") == tool_ev["call_id"]
    assert final_ev["content"] == _BASH_FINAL
    assert final_ev.get("already_streamed") is True


async def test_bash_tool_result_preserves_large_output(monkeypatch) -> None:
    bash_output = "output\n" * 1000
    monkeypatch.setattr(
        "app.runtime.tools.registry.execute",
        lambda _name, _arguments: bash_output,
    )
    llm = ScriptedLLM(tool_then_text(bash_call("generate output"), _BASH_FINAL))

    events = await _collect("run: generate output", llm=llm, tools=_bash_tools())

    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert result_ev["tool"] == "bash"
    assert result_ev["result"] == bash_output


async def test_vault_search_path_returns_saved_fact(tmp_path) -> None:
    """Scripted recall tool call; result includes seeded KB fact."""
    store.rebind(tmp_path / "recall_loop.db")
    from app.runtime.memory.vault.write import add_entity
    add_entity("web", "topic/vendor-deadline", "The Q3 vendor onboarding deadline is October 15, 2026.")
    llm = ScriptedLLM(
        tool_then_text(
            memory_search_call("Q3 vendor onboarding deadline"),
            _RECALL_FINAL,
        )
    )
    events = await _collect(
        "What is the Q3 vendor onboarding deadline?",
        llm=llm,
        tools=_memory_tools(),
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    tool_ev = next(e for e in events if e["kind"] == "tool")
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert tool_ev["tool"] == "memory"
    assert "vendor" in tool_ev["args"]["query"].lower() or "deadline" in tool_ev["args"]["query"].lower()
    assert "October 15, 2026" in result_ev["result"]
    assert result_ev["error"] is False
    assert _final(events)["content"] == _RECALL_FINAL


async def test_bash_error_result_sets_error_flag() -> None:
    """A tool that returns an ``Error:`` string must flag ``error=True``."""
    llm = ScriptedLLM(tool_then_text(bash_call(""), _BASH_FINAL))
    events = await _collect("run:", llm=llm, tools=_bash_tools())
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert result_ev["error"] is True
    assert result_ev["result"].startswith("Error")


async def test_history_rebuilt_so_new_bash_turn_still_calls_tool(tmp_path) -> None:
    """A prior completed bash turn in SQLite history must not suppress a fresh one."""
    history = _session_history(
        tmp_path,
        {"type": "user", "content": "run: echo 4"},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 4"}},
        {"type": "tool_output", "content": "4"},
        {"type": "final", "content": _BASH_FINAL},
        db_name="hist_bash.db",
    )
    llm = ScriptedLLM(tool_then_text(bash_call("echo 10"), _BASH_FINAL))
    events = await _collect(
        "run: echo 10", llm=llm, tools=_bash_tools(), history=history
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    assert next(e for e in events if e["kind"] == "tool")["args"] == {"command": "echo 10"}
    assert next(e for e in events if e["kind"] == "tool_result")["result"].strip() == "10"


# --- adversarial paths --------------------------------------------------


async def test_repeated_tool_calls_keep_results_adjacent_before_loop_nudge() -> None:
    llm = _RecordingScripted(
        [bash_call("echo 1", id=f"call_{i}") for i in range(5)]
        + [text_reply("stopped")]
    )
    events = await _collect("repeat", llm=llm, tools=_bash_tools(), max_iterations=6)
    assert _final(events)["content"] == "stopped"
    messages = llm.captured[-1]
    for index, message in enumerate(messages):
        if message.get("tool_calls"):
            ids = [call["id"] for call in message["tool_calls"]]
            assert [m["tool_call_id"] for m in messages[index + 1:index + 1 + len(ids)]] == ids
    assert any("repeating" in str(m.get("content")) for m in messages if m["role"] == "system")


async def test_max_iterations_force_final_when_budget_exhausted() -> None:
    """A client that always requests tools gets a forced no-tools final round."""
    llm = ScriptedLLM(
        [
            bash_call("echo 1", id="call_a"),
            bash_call("echo 1", id="call_b"),
            text_reply("Best effort answer after tool budget."),
        ]
    )
    events = await _collect(
        "keep calling tools",
        llm=llm,
        tools=_bash_tools(),
        max_iterations=2,
    )
    kinds = _kinds(events, drop_delta=True)
    assert kinds[:4] == ["tool", "tool_result", "tool", "tool_result"]
    assert kinds[-1] == "final"
    final = _final(events)
    assert "Best effort" in final["content"]
    assert final.get("metrics", {}).get("force_final") is True


async def test_max_iterations_force_final_failure_surfaces_error() -> None:
    """If the force-final LLM round fails, surface an error event."""
    llm = ScriptedLLM(
        [
            bash_call("echo 1", id="call_a"),
            bash_call("echo 1", id="call_b"),
        ]
    )
    events = await _collect(
        "keep calling tools",
        llm=llm,
        tools=_bash_tools(),
        max_iterations=2,
    )
    assert events[-1]["kind"] == "error"
    assert "max tool iterations" in events[-1]["message"]


async def test_thinking_emitted_when_content_accompanies_tool_calls() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="Let me run that.",
                tool_calls=[
                    ToolCall(
                        id="call_think",
                        name="bash",
                        arguments={"command": "echo 4"},
                    )
                ],
            ),
            text_reply("Done: 4"),
        ]
    )
    events = await _collect("plan then run", llm=llm, tools=_bash_tools())
    assert _kinds(events, drop_delta=True) == ["thinking", "assistant_progress", "tool", "tool_result", "final"]
    assert events[0] == {"kind": "thinking", "content": "Let me run that."}
    assert next(e for e in events if e["kind"] == "tool")["args"] == {"command": "echo 4"}
    assert _final(events)["content"] == "Done: 4"


async def test_thinking_emitted_from_provider_reasoning_without_tool_calls() -> None:
    """A provider-native reasoning summary (e.g. Codex) surfaces as `thinking`
    even on a plain final answer with no tool calls — distinct from the
    pre-tool-call-commentary heuristic above."""
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="The answer is 4.",
                tool_calls=[],
                reasoning="2 + 2 = 4, a basic addition.",
            ),
        ]
    )
    events = await _collect("what is 2+2", llm=llm, tools=[])
    assert _kinds(events, drop_delta=True) == ["thinking", "final"]
    thinking = next(e for e in events if e["kind"] == "thinking")
    assert thinking == {"kind": "thinking", "content": "2 + 2 = 4, a basic addition."}
    assert _final(events)["content"] == "The answer is 4."


async def test_llm_exception_surfaces_as_error_event() -> None:
    events = await _collect("boom", llm=_BoomMock(), tools=_bash_tools())
    assert [e["kind"] for e in events] == ["error"]
    assert "LLM request failed" in events[0]["message"]
    assert "upstream blew up" in events[0]["message"]


async def test_empty_tool_list_keeps_text_only_path() -> None:
    """No tools advertised -> scripted text reply as ``final``."""
    llm = ScriptedLLM([text_reply(_DEFAULT_REPLY)])
    events = await _collect("run: echo 2", llm=llm, tools=[])
    assert _kinds(events, drop_delta=True) == ["final"]
    assert _final(events)["content"] == _DEFAULT_REPLY


# --- stub LLM clients ---------------------------------------------------


class _BoomMock:
    """Simulates an upstream LLM failure (e.g. ``LLMRequestError``)."""

    async def complete(self, messages, tools=None):
        raise RuntimeError("upstream blew up")


class _RecordingScripted:
    """ScriptedLLM that also records messages passed to each ``complete``."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._inner = ScriptedLLM(responses)
        self.captured: list[list[dict[str, Any]]] = []

    async def complete(self, messages, tools=None):
        self.captured.append(list(messages))
        return await self._inner.complete(messages, tools)


# --- turn-scoped ids / setup errors / user_message=None ----------------


async def test_empty_ids_stay_distinct_across_two_rounds() -> None:
    """Empty tool-call ids must not collide between completion rounds."""
    empty = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id="", name="bash", arguments={"command": "echo 1"})
        ],
    )
    mock = _RecordingScripted([empty, empty, text_reply("done")])
    events = await _collect(
        "run twice", llm=mock, tools=_bash_tools(), max_iterations=4
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
    events = await _collect("hi", tools=_bash_tools())
    assert [e["kind"] for e in events] == ["error"]
    assert "setup" in events[0]["message"].lower()
    assert "bad provider config" in events[0]["message"]


async def test_get_openai_tools_failure_surfaces_as_error_event(monkeypatch) -> None:
    """A failing ``get_openai_tools`` during setup yields an error event."""
    def _boom() -> None:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr("app.runtime.agent.loop.get_openai_tools", _boom)
    events = await _collect("hi", llm=ScriptedLLM([text_reply(_DEFAULT_REPLY)]))
    assert [e["kind"] for e in events] == ["error"]
    assert "setup" in events[0]["message"].lower()
    assert "registry exploded" in events[0]["message"]


async def test_user_message_none_does_not_duplicate_history_user(tmp_path) -> None:
    """``user_message=None`` must not append a second trailing user message."""
    history = _session_history(
        tmp_path,
        {"type": "user", "content": "the new question"},
        db_name="hist_none.db",
    )
    recorder = _RecordingScripted([text_reply(_DEFAULT_REPLY)])
    events = await _collect(None, llm=recorder, tools=_bash_tools(), history=history)
    assert _kinds(events, drop_delta=True) == ["final"]
    msgs = recorder.captured[-1]
    users = [m for m in msgs if m["role"] == "user"]
    assert users == [{"role": "user", "content": "the new question"}]


@pytest.mark.parametrize("at_limit", [False, True])
@pytest.mark.parametrize("direct_request", [None, "explicit worker task"])
async def test_turn_records_goal_from_input_or_history(
    tmp_path, monkeypatch, at_limit, direct_request
) -> None:
    from datetime import date
    from app.core import config
    from app.runtime.memory.vault.paths import timeline_path

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    store.rebind(tmp_path / "goal.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    store.update_settings({"memory_vault_enabled": True})
    store.append_session_history(sid, {"type": "user", "content": "old question"})
    store.append_session_history(sid, {"type": "assistant", "content": "old answer"})
    store.append_session_history(sid, {"type": "user", "content": "current question"})
    extraction = []
    reviews = []
    monkeypatch.setattr(
        "app.runtime.memory.vault.extract.schedule_extraction",
        lambda *args: extraction.append(args),
    )
    monkeypatch.setattr(
        "app.runtime.agent.learning.schedule_learning_review",
        lambda **kwargs: reviews.append(kwargs),
    )
    llm = _RecordingScripted([text_reply("done")])
    events = await _collect(
        direct_request, llm=llm, tools=[], session_id=sid, agent_id="main",
        history=store.get_session_history(sid), max_iterations=0 if at_limit else 2,
    )
    assert _final(events)["content"] == "done"
    expected = direct_request or "current question"
    raw = timeline_path("web", date.today().isoformat()).read_text()
    assert f"- Goal: {expected}\n" in raw
    assert extraction[0][2] == expected
    assert reviews[0]["user_message"] == expected
    users = [m for m in llm.captured[-1] if m["role"] == "user"]
    assert len(users) == (3 if direct_request else 2)
    if at_limit:
        assert llm.captured[-1][-1]["role"] == "system"


async def test_provider_reported_smaller_window_controls_retry():
    class SmallerWindowLLM(ContextCapturingLLM):
        context_window = 1_000_000

        async def complete(self, messages, tools=None):
            if not self.prompts:
                self.prompts.append(list(messages))
                raise RuntimeError("This model's maximum context length is 128,000 tokens; requested 500000 tokens")
            return await super().complete(messages, tools)

    llm = SmallerWindowLLM()
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions",
                            history=[{"type": "user", "content": "X" * 2_000_000}])
    assert _final(events)["content"] == "Fits now."
    assert len(llm.prompts) == 2
    assert llm.context_window == 128_000
    assert estimate_prompt_tokens(llm.prompts[1]) <= prompt_budget(128_000)


async def test_forced_final_also_compacts_before_request():
    llm = ContextCapturingLLM()
    events = await _collect("Continue.", llm=llm, tools=[], system_prompt="Instructions",
                            history=[{"type": "user", "content": "X" * 2_000_000}], max_iterations=0)
    assert _final(events)["metrics"]["compressed"] is True
    assert len(llm.prompts) == 1
    assert estimate_prompt_tokens(llm.prompts[0]) <= prompt_budget(128_000)
    assert any(message.get("content") == "Continue." for message in llm.prompts[0])


async def test_error_flag_requires_error_colon_prefix(monkeypatch) -> None:
    """A result starting with ``Error`` but not ``Error:`` is not an error."""
    monkeypatch.setattr(
        "app.runtime.tools.registry.execute",
        lambda name, args: "Errorless computation succeeded",
    )
    llm = ScriptedLLM(tool_then_text(bash_call("echo 2"), _BASH_FINAL))
    events = await _collect("run: echo 2", llm=llm, tools=_bash_tools())
    result_ev = next(e for e in events if e["kind"] == "tool_result")
    assert result_ev["error"] is False
    assert result_ev["result"] == "Errorless computation succeeded"


# --- swarm delegation (subagent model: parent continues) ---------------


def _delegate_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "delegate"}}]


def _delegate_call(
    agent_id: str = "ops", reason: str = "ops task", id: str = "call_delegate"
) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=id,
                name="delegate",
                arguments={"agent_id": agent_id, "reason": reason},
            )
        ],
    )


async def test_cancelling_parent_stops_parallel_subagents(monkeypatch) -> None:
    monkeypatch.setattr("app.runtime.tools.registry.execute", lambda name, args: f"Delegated to {args['agent_id']}")
    started = asyncio.Event()
    cancelled = set()
    entered = set()

    class BlockingChild:
        def __init__(self, name):
            self.name = name

        async def complete(self, messages, tools=None):
            entered.add(self.name)
            if len(entered) == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(self.name)

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None: BlockingChild(agent_id))
    parent = ScriptedLLM([LLMResponse(content=None, tool_calls=[
        ToolCall(id="d1", name="delegate", arguments={"agent_id": "ops", "reason": "one"}),
        ToolCall(id="d2", name="delegate", arguments={"agent_id": "writer", "reason": "two"}),
    ])])

    async def consume():
        async for _ in run_turn("delegate", llm=parent, tools=_delegate_tools(), agent_id="main", enable_atg=False):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    try:
        await asyncio.wait_for(task, 3)
    except asyncio.CancelledError:
        pass
    assert cancelled == {"ops", "writer"}


async def test_successful_delegate_runs_subagent_and_parent_continues(
    monkeypatch, tmp_path
) -> None:
    """After a successful delegate, the subagent runs and its output becomes
    the delegate tool result; the parent loop *continues* to a final answer."""
    store.rebind(tmp_path / "delegate_sub.db")
    monkeypatch.setattr(
        "app.runtime.tools.registry.execute",
        lambda name, args: "Delegated to ops",
    )
    # Parent: call delegate, then give a final answer.
    parent_llm = ScriptedLLM([_delegate_call(), text_reply("Done with ops help.")])
    # Subagent: single text reply (its final output).
    subagent_llm = ScriptedLLM([text_reply("ops handled it")])
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm", lambda agent_id=None: subagent_llm
    )
    events = await _collect(
        "ask ops to help",
        llm=parent_llm,
        tools=_delegate_tools(),
        agent_id="main",
    )
    kinds = _kinds(events, drop_delta=True)
    # delegate marker present, parent reaches a final (does NOT stop).
    assert "delegate" in kinds
    assert kinds[-1] == "final"
    assert _final(events)["content"] == "Done with ops help."
    # The delegate tool result carries the subagent's output.
    result_ev = next(
        e for e in events if e["kind"] == "tool_result" and e["tool"] == "delegate"
    )
    assert "ops handled it" in result_ev["result"]
    assert result_ev["error"] is False


async def test_delegate_streams_subagent_events_before_tool_result(
    monkeypatch, tmp_path
) -> None:
    """Nested tool events must appear before the parent delegate tool_result."""
    store.rebind(tmp_path / "delegate_stream.db")

    def _exec(name: str, args: dict) -> str:
        if name == "delegate":
            return "Delegated to ops"
        if name == "bash":
            return "up 1 day"
        return f"Error: unexpected tool {name}"

    monkeypatch.setattr("app.runtime.tools.registry.execute", _exec)

    parent_llm = ScriptedLLM([_delegate_call(), text_reply("Parent wrap-up.")])
    # Ops: one bash call then a final answer.
    ops_llm = ScriptedLLM(tool_then_text(bash_call("uptime"), "ops done"))

    def _llm_for(agent_id: str | None = None):
        return ops_llm if agent_id == "ops" else parent_llm

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _llm_for)

    events = await _collect(
        "ask ops",
        llm=parent_llm,
        tools=_delegate_tools() + _bash_tools(),
        agent_id="main",
    )
    # Live order: start markers, then nested tool work, then delegate tool_result.
    first_nested_tool = next(
        i
        for i, e in enumerate(events)
        if e["kind"] == "tool" and e.get("tool") == "bash"
    )
    delegate_result_i = next(
        i
        for i, e in enumerate(events)
        if e["kind"] == "tool_result" and e.get("tool") == "delegate"
    )
    assert "subagent_start" in {e["kind"] for e in events}
    assert first_nested_tool < delegate_result_i
    assert any(e["kind"] == "subagent_done" for e in events)
    # Parent delegate call_id is stamped on swarm lifecycle + nested events.
    start_ev = next(e for e in events if e["kind"] == "subagent_start")
    assert start_ev.get("delegate_call_id") == "call_delegate"
    bash_ev = next(e for e in events if e["kind"] == "tool" and e.get("tool") == "bash")
    assert bash_ev.get("delegate_call_id") == "call_delegate"
    assert bash_ev.get("call_id")  # nested tool keeps its own call_id
    assert bash_ev["call_id"] != "call_delegate"



async def test_subagent_reasoning_surfaces_as_tagged_thinking_event(
    monkeypatch, tmp_path
) -> None:
    """A subagent's provider-native reasoning (e.g. Codex) must reach the
    parent's event stream as a `thinking` event tagged with the subagent's
    agent_id/delegate_call_id — same path the swarm detail panel and
    session-history replay already render generically."""
    store.rebind(tmp_path / "delegate_reasoning.db")

    def _exec(name: str, args: dict) -> str:
        if name == "delegate":
            return "Delegated to ops"
        if name == "bash":
            return "up 1 day"
        return f"Error: unexpected tool {name}"

    monkeypatch.setattr("app.runtime.tools.registry.execute", _exec)

    parent_llm = ScriptedLLM([_delegate_call(), text_reply("Parent wrap-up.")])
    ops_llm = ScriptedLLM([
        bash_call("uptime"),
        LLMResponse(content="ops done", tool_calls=[], reasoning="Checking uptime output."),
    ])

    def _llm_for(agent_id: str | None = None):
        return ops_llm if agent_id == "ops" else parent_llm

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _llm_for)

    events = await _collect(
        "ask ops",
        llm=parent_llm,
        tools=_delegate_tools() + _bash_tools(),
        agent_id="main",
    )
    sub_thinking = next(
        e for e in events if e["kind"] == "thinking" and e.get("agent_id") == "ops"
    )
    assert sub_thinking["content"] == "Checking uptime output."
    assert sub_thinking.get("delegate_call_id") == "call_delegate"
    assert sub_thinking.get("subagent") is True


async def test_failed_delegate_continues_tool_loop(monkeypatch) -> None:
    """A rejected delegate is a normal tool error; loop keeps iterating."""
    monkeypatch.setattr(
        "app.runtime.tools.registry.execute",
        lambda name, args: "Error: 'ghost' is not a member of this session",
    )
    llm = ScriptedLLM(
        [
            _delegate_call(agent_id="ghost", reason="", id="call_bad"),
            text_reply("I'll handle it myself."),
        ]
    )
    events = await _collect(
        "delegate to ghost",
        llm=llm,
        tools=_delegate_tools(),
        agent_id="main",
    )
    assert _kinds(events, drop_delta=True) == ["tool", "tool_result", "final"]
    assert not any(e["kind"] == "delegate" for e in events)
    assert _final(events)["content"] == "I'll handle it myself."


async def test_nested_worker_leaves_composer_steer_for_parent(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.runtime.agent.subagent import drain_subagent_turn
    from app.services import chat

    inbox = [{"content": "Keep production safe", "steer_id": "parent-guidance"}]
    active = SimpleNamespace(steer_inbox=inbox, _steer_lock=threading.Lock())
    monkeypatch.setattr(chat, "get_active_session_turn", lambda sid: active)

    def drain(sid):
        items = list(inbox)
        inbox.clear()
        return items

    monkeypatch.setattr(chat, "drain_session_steers", drain)

    def child_run(prompt, **kwargs):
        return run_turn(prompt, system_prompt="Test worker.", enable_atg=False, **kwargs)

    child_events = [
        event
        async for event, _ in drain_subagent_turn(
            "ops", from_agent_id="main", reason="Check nodes",
            user_request="Inspect the cluster", session_id="fixture",
            llm=ScriptedLLM([text_reply("Worker finished")]), tools=[],
            run_turn_fn=child_run,
        )
    ]
    assert len(inbox) == 1
    assert not any(event["kind"] == "steer" for event in child_events)
    assert child_events[-1]["kind"] == "subagent_final"

    parent_events = await _collect(
        "Review worker results", session_id="fixture", agent_id="main",
        llm=ScriptedLLM([text_reply("Parent applied the guidance")]), tools=[],
    )
    assert inbox == []
    steers = [event for event in parent_events if event["kind"] == "steer"]
    assert len(steers) == 1
    assert steers[0]["content"] == "Keep production safe"
    assert _final(parent_events)["content"] == "Parent applied the guidance"

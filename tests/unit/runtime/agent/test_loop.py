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

from typing import Any

from app.runtime.agent.loop import _truncate_result, run_turn
from app.runtime.llm import LLMConfigError
from app.runtime.llm.base import LLMResponse, ToolCall
from app.services import store
from tests.fakes.llm import ScriptedLLM, bash_call, recall_call, text_reply, tool_then_text

_DEFAULT_REPLY = "Ready to help."
_BASH_FINAL = "The command finished."
_RECALL_FINAL = "I found the relevant knowledge base entry."


def test_tool_result_caps_are_tool_appropriate() -> None:
    catalog = "skill-1: useful skill\n" * 300
    assert _truncate_result(catalog, tool_name="list_skills") == catalog

    paginated = "header\n" + ("content\n" * 4000) + "Continue with offset=42."
    shortened = _truncate_result(paginated, tool_name="read_file")
    assert len(shortened) < len(paginated)
    assert "Continue with offset=42." in shortened
    assert "Continue with Continue" not in shortened
    assert "truncated" not in shortened

    unbounded = "output\n" * 1000
    clipped = _truncate_result(unbounded, tool_name="bash")
    assert len(clipped) < len(unbounded)
    assert "[truncated," in clipped


def _bash_tools() -> list[dict[str, Any]]:
    """Minimal OpenAI tool schema advertising bash."""
    return [{"type": "function", "function": {"name": "bash"}}]


def _recall_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "recall"}}]


async def _collect(user_message: str | None, **kw: Any) -> list[dict[str, Any]]:
    """Drain the ``run_turn`` async generator into a list of events."""
    # Unit tests use scripted LLMs — keep ATG off unless a test opts in.
    kw.setdefault("enable_atg", False)
    return [ev async for ev in run_turn(user_message, **kw)]


def _kinds(events: list[dict[str, Any]], *, drop_delta: bool = False) -> list[str]:
    kinds = [e["kind"] for e in events]
    if drop_delta:
        return [k for k in kinds if k != "delta"]
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
    assert result_ev.get("call_id") == tool_ev["call_id"]
    assert final_ev["content"] == _BASH_FINAL
    assert final_ev.get("already_streamed") is True


async def test_recall_path_returns_seeded_fact(tmp_path) -> None:
    """Scripted recall tool call; result includes seeded KB fact."""
    store.rebind(tmp_path / "recall_loop.db")
    llm = ScriptedLLM(
        tool_then_text(
            recall_call("Q3 vendor onboarding deadline"),
            _RECALL_FINAL,
        )
    )
    events = await _collect(
        "What is the Q3 vendor onboarding deadline?",
        llm=llm,
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
    assert _kinds(events, drop_delta=True) == ["thinking", "tool", "tool_result", "final"]
    assert events[0] == {"kind": "thinking", "content": "Let me run that."}
    assert next(e for e in events if e["kind"] == "tool")["args"] == {"command": "echo 4"}
    assert _final(events)["content"] == "Done: 4"


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

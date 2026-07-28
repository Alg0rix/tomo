"""Integration: web chat SSE wiring over the real agent loop.

Drives ``run_session_turn`` end to end against a per-test temp SQLite DB.
Product LLM is settings-backed (OpenAICompat only); these tests inject
:class:`~tests.fakes.llm.ScriptedLLM` via ``get_llm`` so the stream contract
is verified without a network or API key.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from app.runtime.llm.base import LLMResponse, ToolCall
from app.services import run_session_turn, store
from tests.fakes.llm import ScriptedLLM, bash_call, text_reply, tool_then_text

_DEFAULT_REPLY = "Ready to help."
_BASH_FINAL = "The command finished."


@pytest.fixture(autouse=True)
def _inject_scripted_llm(monkeypatch) -> None:
    """Default: shared text-only queue (multi-turn tests share one client)."""
    client = ScriptedLLM([text_reply(_DEFAULT_REPLY)] * 20)
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: client,
    )


def _patch_llm(monkeypatch, responses: list[LLMResponse]):
    """Replace ``get_llm`` with a shared ScriptedLLM over ``responses``."""
    client = ScriptedLLM(list(responses))
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: client,
    )
    return client


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Split a raw SSE buffer into ``(event_name, data)`` pairs."""
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name: str | None = None
        data = "{}"
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if name:
            events.append((name, json.loads(data)))
    return events


async def _collect(session_id: str, message: str) -> list[tuple[str, dict]]:
    """Drain one ``run_session_turn`` stream into parsed SSE events."""
    chunks: list[str] = []
    async for chunk in run_session_turn(session_id, message, "web", start_seq=0):
        chunks.append(chunk)
    return _parse_sse("".join(chunks))


def _names(events: list[tuple[str, dict]]) -> list[str]:
    return [n for n, _ in events]


def _data(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [d for n, d in events if n == name]


async def test_plain_turn_emits_done_and_persists_user_final(tmp_path) -> None:
    store.rebind(tmp_path / "chat_plain.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    events = await _collect(sid, "hello there")

    # Well-formed turn envelope: leading busy-true state, turn.start, a delta
    # + done, then a trailing busy-false state.
    assert _names(events)[0] == "state"
    assert events[0][1] == {"agent_id": "main", "busy": True}
    assert "turn.start" in _names(events)
    assert _data(events, "delta"), "final content is delivered as a delta chunk"
    assert _data(events, "done"), "expected a done event"
    assert _data(events, "done")[0]["agent_id"] == "main"
    # turn.start and done share the same turn_id (P2: restore turn_id on done).
    assert _data(events, "turn.start")[0]["turn_id"] == _data(events, "done")[0]["turn_id"]
    states = _data(events, "state")
    assert states[-1] == {"agent_id": "main", "busy": False}

    history = store.get_session_history(sid)
    types = [h["type"] for h in history]
    assert types[0] == "user"
    assert history[0]["content"] == "hello there"
    assert "final" in types
    # plain path used no tools
    assert "tool_call" not in types
    assert "tool_output" not in types
    # First user message auto-resolves a provisional chat name on the stream.
    # Without an LLM title mock, the upgrade soft-fails and keeps the provisional.
    assert store.get_session(sid)["title"] == "hello there"
    session_ev = _data(events, "session")
    assert session_ev and session_ev[0]["title"] == "hello there"
    assert session_ev[0]["session_id"] == sid


async def test_llm_upgrades_session_title_after_first_final(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "chat_title_llm.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    async def _fake_title(user_text: str, assistant_text: str, *, llm=None) -> str:
        assert "hello there" in user_text
        assert assistant_text
        return "Greeting Chat"

    monkeypatch.setattr("app.channels.web.generate_session_title", _fake_title)

    events = await _collect(sid, "hello there")
    titles = [d["title"] for d in _data(events, "session")]
    assert titles == ["hello there", "Greeting Chat"]
    assert store.get_session(sid)["title"] == "Greeting Chat"

    events2 = await _collect(sid, "follow up")
    assert _data(events2, "session") == []
    assert store.get_session(sid)["title"] == "Greeting Chat"


async def test_llm_title_failure_keeps_provisional(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "chat_title_fail.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    async def _none_title(user_text: str, assistant_text: str, *, llm=None):
        return None

    monkeypatch.setattr("app.channels.web.generate_session_title", _none_title)

    events = await _collect(sid, "ship the darkroom fix")
    assert [d["title"] for d in _data(events, "session")] == ["ship the darkroom fix"]
    assert store.get_session(sid)["title"] == "ship the darkroom fix"
    assert _data(events, "done")


async def test_second_turn_does_not_reemit_session_title(tmp_path) -> None:
    store.rebind(tmp_path / "chat_title_once.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    await _collect(sid, "first question about billing")
    events = await _collect(sid, "follow up")
    assert _data(events, "session") == []
    assert store.get_session(sid)["title"] == "first question about billing"


async def test_bash_turn_emits_tool_events_and_persists_entries(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "chat_bash.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    _patch_llm(monkeypatch, tool_then_text(bash_call("echo 4"), _BASH_FINAL))

    events = await _collect(sid, "run: echo 4")

    # tool -> tool_result -> (delta) -> done
    assert _names(events)[:1] == ["state"]
    assert "turn.start" in _names(events)
    assert [n for n in _names(events) if n in ("tool", "tool_result")] == [
        "tool",
        "tool_result",
    ]
    tool_ev = _data(events, "tool")[0]
    assert tool_ev["tool"] == "bash"
    assert tool_ev["args"] == {"command": "echo 4"}
    result_ev = _data(events, "tool_result")[0]
    assert result_ev["result"].strip() == "4"
    assert result_ev["error"] is False
    assert _data(events, "done")

    history = store.get_session_history(sid)
    types = [h["type"] for h in history]
    assert types[0] == "user"
    assert types.count("tool_call") == 1
    assert types.count("tool_output") == 1
    assert "final" in types
    # persisted tool_call carries the function + params; tool_output the result
    call = next(h for h in history if h["type"] == "tool_call")
    assert call["function"] == "bash"
    assert call["params"] == {"command": "echo 4"}
    out = next(h for h in history if h["type"] == "tool_output")
    assert out["content"].strip() == "4"
    assert out["error"] is False


async def test_coordinator_turn_live_swarm_membership(tmp_path, monkeypatch) -> None:
    """A bash turn stays on the coordinator; swarm membership is live-enabled."""
    store.rebind(tmp_path / "chat_swarm.db")
    sid = store.create_swarm_session(["main", "research"], user_id="web")
    _patch_llm(monkeypatch, tool_then_text(bash_call("echo 7"), _BASH_FINAL))

    await _collect(sid, "run: echo 7")

    session = store.get_session(sid)
    assert session is not None
    assert session.get("is_swarm") is True
    assert set(session["agent_ids"]) == set(store.list_enabled_agent_ids())
    assert session["coordinator_id"] == "main"
    # no handoff — only the coordinator produced a final entry
    finals = [h for h in store.get_session_history(sid) if h["type"] == "final"]
    assert finals and finals[0]["agent_id"] == "main"


async def test_unknown_session_yields_error_event(tmp_path) -> None:
    store.rebind(tmp_path / "chat_missing.db")
    events = await _collect("ses_does_not_exist", "hi")
    assert _names(events) == ["error"]
    assert "not found" in _data(events, "error")[0]["message"].lower()


async def test_loop_error_clears_busy_after_stream_drains(tmp_path, monkeypatch) -> None:
    """A loop ``error`` event surfaces with its message and clears busy on drain.

    Forces a setup failure (``get_llm`` raises) so the loop yields one ``error``
    event; the stream still drains to a trailing busy-false ``state`` and the
    coordinator's busy flag is cleared in the store (P1: busy cleared in a
    ``finally`` that does not yield).
    """
    store.rebind(tmp_path / "chat_error_busy.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    def _boom(agent_id=None) -> None:
        raise RuntimeError("forced setup failure")

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _boom)

    events = await _collect(sid, "anything")

    # The server-side error is surfaced as a named `error` SSE event carrying
    # the loop message (not a transport failure).
    assert "error" in _names(events)
    assert "forced setup failure" in _data(events, "error")[0]["message"]
    # The trailing busy-false state is still emitted after the try/finally.
    states = _data(events, "state")
    assert states[-1] == {"agent_id": "main", "busy": False}
    # The error entry is durable (persist-before-yield) and busy is cleared.
    types = [h["type"] for h in store.get_session_history(sid)]
    assert "error" in types
    assert store.get_agent("main")["busy"] is False


async def test_delegate_tool_runs_subagent_and_parent_continues(
    tmp_path, monkeypatch
) -> None:
    """Coordinator ``delegate`` → subagent runs in-loop → output fed back →
    parent *continues* to its own final answer."""
    store.rebind(tmp_path / "chat_delegate.db")
    sid = store.create_swarm_session(["main", "ops"], user_id="web")

    coord = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_d",
                        name="delegate",
                        arguments={"agent_id": "ops", "reason": "ops work"},
                    )
                ],
            ),
            text_reply("Thanks Ops — disk looks fine."),
        ]
    )
    ops = ScriptedLLM([text_reply("Ops reporting: disk looks fine.")])

    def _llm(agent_id=None):
        if agent_id == "ops":
            return ops
        return coord

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _llm)

    events = await _collect(sid, "please have ops check the disk")

    assert "delegate" in _names(events)
    handoff = _data(events, "delegate")[0]
    assert handoff["from"] == "main"
    assert handoff["to"] == "ops"
    assert handoff.get("reason") in ("ops work", "delegate")
    assert handoff.get("task") in ("ops work", "delegate")
    assert handoff.get("parallel_index") == 1
    assert handoff.get("parallel_total") == 1

    # New: subagent_start and subagent_done lifecycle events.
    assert "subagent_start" in _names(events)
    sa_start = _data(events, "subagent_start")[0]
    assert sa_start["agent_id"] == "ops"
    assert sa_start.get("task") in ("ops work", "delegate")
    assert "subagent_done" in _names(events)
    sa_done = _data(events, "subagent_done")[0]
    assert sa_done["agent_id"] == "ops"
    assert sa_done["status"] == "ok"

    # The subagent's answer appears as deltas attributed to ops.
    deltas = _data(events, "delta")
    ops_deltas = [d for d in deltas if d.get("agent_id") == "ops"]
    assert ops_deltas, "subagent deltas should be attributed to ops"
    assert "disk" in "".join(d.get("content", "") for d in ops_deltas).lower()

    # The parent continues and its final is the terminal ``done``.
    dones = _data(events, "done")
    assert dones, "expected at least one done event"
    assert dones[-1]["agent_id"] == "main"
    assert "disk" in (dones[-1].get("content") or "").lower() or "Ops" in (
        dones[-1].get("content") or ""
    )

    history = store.get_session_history(sid)
    types = [h["type"] for h in history]
    assert "delegate" in types
    assert "subagent_start" in types
    assert "subagent_done" in types
    assert types.count("tool_call") >= 1
    # Delegate entry stores metadata in params (not loose keys).
    del_entry = next(h for h in history if h["type"] == "delegate")
    assert del_entry["agent_id"] == "ops"
    assert del_entry["params"]["from"] == "main"
    assert del_entry["params"]["to"] == "ops"
    assert del_entry["params"]["task"] in ("ops work", "delegate")
    # subagent_start entry persists name/task/parallel fields.
    sa_start_h = next(h for h in history if h["type"] == "subagent_start")
    assert sa_start_h["agent_id"] == "ops"
    assert sa_start_h["params"]["name"] == "Ops"
    assert sa_start_h["params"]["task"] in ("ops work", "delegate")
    assert sa_start_h["params"]["parallel_index"] == 1
    assert sa_start_h["params"]["parallel_total"] == 1
    # subagent_done entry persists status.
    sa_done_h = next(h for h in history if h["type"] == "subagent_done")
    assert sa_done_h["agent_id"] == "ops"
    assert sa_done_h["params"]["status"] == "ok"
    finals = [h for h in history if h["type"] == "final"]
    assert finals and finals[-1]["agent_id"] == "main"
    assert store.get_agent("main")["busy"] is False
    assert store.get_agent("ops")["busy"] is False


async def test_parallel_delegation_emits_swarm_events(
    tmp_path, monkeypatch
) -> None:
    """Two ``delegate`` calls in one round → 2 delegate events, 2
    subagent_start, 2 subagent_done, correct parallel_index/total."""
    store.rebind(tmp_path / "chat_parallel.db")
    sid = store.create_swarm_session(
        ["main", "ops", "research"], user_id="web"
    )

    coord = ScriptedLLM(
        [
            LLMResponse(
                content="Dispatching ops and research in parallel.",
                tool_calls=[
                    ToolCall(
                        id="call_d1",
                        name="delegate",
                        arguments={"agent_id": "ops", "reason": "check disk"},
                    ),
                    ToolCall(
                        id="call_d2",
                        name="delegate",
                        arguments={"agent_id": "research", "reason": "find docs"},
                    ),
                ],
            ),
            text_reply("Both done — disk fine, docs found."),
        ]
    )
    ops = ScriptedLLM([text_reply("Ops: disk is fine.")])
    research = ScriptedLLM([text_reply("Research: docs at /help.")])

    def _llm(agent_id=None):
        if agent_id == "ops":
            return ops
        if agent_id == "research":
            return research
        return coord

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _llm)

    events = await _collect(sid, "check disk and find docs")

    # Two delegate events with parallel fields.
    delegates = _data(events, "delegate")
    assert len(delegates) == 2
    for d in delegates:
        assert d["parallel_total"] == 2
        assert d["parallel_index"] in (1, 2)
        assert d.get("task")
    targets = {d["to"] for d in delegates}
    assert targets == {"ops", "research"}

    # Two subagent_start events.
    starts = _data(events, "subagent_start")
    assert len(starts) == 2
    sa_ids = {s["agent_id"] for s in starts}
    assert sa_ids == {"ops", "research"}
    for s in starts:
        assert s["parallel_total"] == 2
        assert s.get("task")

    # Two subagent_done events with ok status.
    dones = _data(events, "subagent_done")
    assert len(dones) == 2
    done_ids = {d["agent_id"] for d in dones}
    assert done_ids == {"ops", "research"}
    for d in dones:
        assert d["status"] == "ok"

    # Parent continues to its own final.
    parent_dones = [
        d for d in _data(events, "done") if d.get("agent_id") == "main"
    ]
    assert parent_dones
    assert "disk" in parent_dones[-1].get("content", "").lower()


async def test_mention_forces_ops_without_coordinator_tools(
    tmp_path, monkeypatch
) -> None:
    """``@ops …`` skips the coordinator tool loop and runs ops directly."""
    store.rebind(tmp_path / "chat_mention.db")
    sid = store.create_swarm_session(["main", "ops"], user_id="web")

    class _OpsOnly:
        """Asserts stripped prompt, then returns a scripted text reply."""

        def __init__(self) -> None:
            self._inner = ScriptedLLM([text_reply("Ops on it.")])

        async def complete(self, messages, tools=None):
            user = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            assert "check disk" in user
            assert not user.lstrip().startswith("@")
            return await self._inner.complete(messages, tools)

        async def stream_complete(self, messages, tools=None):
            async for ev in self._inner.stream_complete(messages, tools):
                yield ev

    class _BoomCoord:
        async def complete(self, messages, tools=None):
            raise AssertionError("coordinator loop must be skipped for @mention")

    ops = _OpsOnly()

    def _llm(agent_id=None):
        if agent_id == "ops":
            return ops
        return _BoomCoord()

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", _llm)

    events = await _collect(sid, "@ops check disk")

    assert _data(events, "turn.start")[0].get("delegate") is True
    assert "delegate" in _names(events)
    assert _data(events, "delegate")[0]["reason"] == "mention"
    assert "tool" not in _names(events)
    assert _data(events, "done")[0]["agent_id"] == "ops"

    history = store.get_session_history(sid)
    assert history[0]["type"] == "user"
    assert history[0]["content"] == "@ops check disk"
    finals = [h for h in history if h["type"] == "final"]
    assert finals and finals[0]["agent_id"] == "ops"


async def test_mention_non_member_falls_through_to_coordinator(
    tmp_path, monkeypatch
) -> None:
    """``@support`` when support is not a session member → coordinator answers."""
    store.rebind(tmp_path / "chat_mention_nonmember.db")
    sid = store.create_swarm_session(["main", "ops"], user_id="web")

    events = await _collect(sid, "@support help me")

    assert _data(events, "turn.start")[0].get("delegate") is False
    assert "delegate" not in _names(events)
    dones = _data(events, "done")
    assert dones and dones[0]["agent_id"] == "main"


async def test_early_close_persists_seen_event_and_clears_busy(tmp_path, monkeypatch) -> None:
    """A mid-turn disconnect (aclose before drain) still clears busy + history.

    Mirrors the stream.py route wiring (``aclosing`` around the turn generator):
    stop iterating right after the ``tool`` event. Persist-before-yield means the
    ``tool_call`` entry is already durable; the aclosing cascade runs
    ``stream_turn_sse``'s ``finally`` and clears busy even though the trailing
    busy-false ``state`` is never yielded (P1 + P2).
    """
    store.rebind(tmp_path / "chat_close_busy.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    _patch_llm(monkeypatch, tool_then_text(bash_call("echo 4"), _BASH_FINAL))

    seen_tool = False
    async with contextlib.aclosing(
        run_session_turn(sid, "run: echo 4", "web", start_seq=0)
    ) as agen:
        async for chunk in agen:
            if chunk.startswith("event: tool\n"):
                seen_tool = True
                break  # simulate a client disconnect right after the tool event
        assert seen_tool, "expected to see the tool event before disconnect"

    # Persist-before-yield: tool_call was durable before the chunk was sent.
    types = [h["type"] for h in store.get_session_history(sid)]
    assert "tool_call" in types
    # aclose cascade cleared busy without the trailing busy-false state.
    assert store.get_agent("main")["busy"] is False

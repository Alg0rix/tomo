"""Integration: web chat SSE wiring over the real agent loop (mock LLM).

Drives ``run_session_turn`` end to end against a per-test temp SQLite DB with
the default mock LLM (no API keys): a plain turn yields a ``done`` event and
persists user + final history; a calculator turn additionally persists
``tool_call`` / ``tool_output`` entries. The SSE stream is drained fully — the
turn generator terminates after the trailing busy-false ``state`` (the
infinite heartbeat is added by the route, not by ``run_session_turn``).
"""

from __future__ import annotations

import contextlib
import json

from app.services import run_session_turn, store


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


async def test_calc_turn_emits_tool_events_and_persists_entries(tmp_path) -> None:
    store.rebind(tmp_path / "chat_calc.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    events = await _collect(sid, "calculate 2 + 2")

    # tool -> tool_result -> (delta) -> done
    assert _names(events)[:1] == ["state"]
    assert "turn.start" in _names(events)
    assert [n for n in _names(events) if n in ("tool", "tool_result")] == [
        "tool",
        "tool_result",
    ]
    tool_ev = _data(events, "tool")[0]
    assert tool_ev["tool"] == "calculator"
    assert tool_ev["args"] == {"expression": "2 + 2"}
    result_ev = _data(events, "tool_result")[0]
    assert result_ev["result"] == "4"
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
    assert call["function"] == "calculator"
    assert call["params"] == {"expression": "2 + 2"}
    out = next(h for h in history if h["type"] == "tool_output")
    assert out["content"] == "4"
    assert out["error"] is False


async def test_coordinator_only_keeps_agent_ids_unchanged(tmp_path) -> None:
    """A swarm session runs only the coordinator; membership is not mutated."""
    store.rebind(tmp_path / "chat_swarm.db")
    sid = store.create_swarm_session(["main", "research"], user_id="web")

    await _collect(sid, "calculate 3 + 4")

    session = store.get_session(sid)
    assert session is not None
    assert session["agent_ids"] == ["main", "research"]
    assert session["coordinator_id"] == "main"
    # only the coordinator produced a final entry
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

    def _boom() -> None:
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


async def test_early_close_persists_seen_event_and_clears_busy(tmp_path) -> None:
    """A mid-turn disconnect (aclose before drain) still clears busy + history.

    Mirrors the stream.py route wiring (``aclosing`` around the turn generator):
    stop iterating right after the ``tool`` event. Persist-before-yield means the
    ``tool_call`` entry is already durable; the aclosing cascade runs
    ``stream_turn_sse``'s ``finally`` and clears busy even though the trailing
    busy-false ``state`` is never yielded (P1 + P2).
    """
    store.rebind(tmp_path / "chat_close_busy.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    seen_tool = False
    async with contextlib.aclosing(
        run_session_turn(sid, "calculate 2 + 2", "web", start_seq=0)
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

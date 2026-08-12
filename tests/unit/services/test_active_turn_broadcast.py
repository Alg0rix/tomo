"""Active-turn broadcast must always deliver the completion sentinel."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import chat as chat_mod
from app.services.chat import SessionTurnBusy, _ActiveTurn, _REPLAY_MAX, get_active_session_turn


def _drain(q: asyncio.Queue) -> list:
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    return got


def _is_caught_up(chunk: str | None) -> bool:
    return bool(chunk and "event: caught_up" in chunk)


@pytest.mark.asyncio
async def test_subscribe_replays_buffered_events() -> None:
    turn = _ActiveTurn(session_id="s_replay")
    # Seed via broadcast so ring buffer is populated.
    for i in range(5):
        turn._broadcast(f"id: {i}\nevent: delta\ndata: {{\"n\":{i}}}\n\n")
    q = turn.subscribe(after_seq=2)
    got = _drain(q)
    # Only seq > 2, then caught_up boundary
    assert len(got) == 3
    assert "id: 3" in got[0]
    assert "id: 4" in got[1]
    assert _is_caught_up(got[2])


@pytest.mark.asyncio
async def test_subscribe_after_zero_replays_all() -> None:
    turn = _ActiveTurn(session_id="s_all")
    turn._broadcast("id: 1\nevent: tool\ndata: {}\n\n")
    turn._broadcast("id: 2\nevent: tool_result\ndata: {}\n\n")
    q = turn.subscribe(after_seq=0)
    got = _drain(q)
    assert len(got) == 3
    assert "id: 1" in got[0]
    assert "id: 2" in got[1]
    assert _is_caught_up(got[2])


@pytest.mark.asyncio
async def test_subscribe_empty_buffer_still_emits_caught_up() -> None:
    """Resume mid-quiet-tool must still enter live mode without waiting for events."""
    turn = _ActiveTurn(session_id="s_empty")
    q = turn.subscribe(after_seq=0)
    got = _drain(q)
    assert len(got) == 1
    assert _is_caught_up(got[0])


@pytest.mark.asyncio
async def test_replay_ring_caps_at_max() -> None:
    turn = _ActiveTurn(session_id="s_cap")
    for i in range(_REPLAY_MAX + 50):
        turn._broadcast(f"id: {i}\nevent: delta\ndata: {{}}\n\n")
    assert len(turn._replay) == _REPLAY_MAX
    # Oldest dropped; first kept seq is 50
    first_seq = turn._replay[0][0]
    assert first_seq == 50


@pytest.mark.asyncio
async def test_broadcast_none_delivered_when_queue_full() -> None:
    turn = _ActiveTurn(session_id="s1")
    q = turn.subscribe()
    # Drain caught_up so we can fill to capacity with dummy chunks.
    while not q.empty():
        q.get_nowait()
    for i in range(q.maxsize):
        q.put_nowait(f"chunk-{i}")
    assert q.full()

    turn.finish()

    # Drain until sentinel — must not hang / miss None.
    got_none = False
    while True:
        item = q.get_nowait()
        if item is None:
            got_none = True
            break
    assert got_none


@pytest.mark.asyncio
async def test_put_none_on_full_queue_makes_room() -> None:
    q: asyncio.Queue = asyncio.Queue(maxsize=4)
    for i in range(4):
        q.put_nowait(f"x{i}")
    _ActiveTurn._put(q, None)
    # Exactly one None somewhere; last get should be able to find it.
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert None in items


@pytest.mark.asyncio
async def test_start_session_turn_rejects_when_already_active() -> None:
    chat_mod._active_turns.clear()
    fake_session = {
        "id": "ses_busy",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }

    async def _empty_sse(*_a, **_k):
        if False:  # pragma: no cover
            yield ""
        return

    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod, "stream_turn_sse", _empty_sse),
    ):
        turn, _q = await chat_mod.start_session_turn("ses_busy", "hi", "web")
        assert get_active_session_turn("ses_busy") is turn
        with pytest.raises(SessionTurnBusy):
            await chat_mod.start_session_turn("ses_busy", "again", "web")
        # Finish the first turn cleanly.
        if turn.task:
            await turn.task
        assert get_active_session_turn("ses_busy") is None


@pytest.mark.asyncio
async def test_cancel_session_turn_cancels_running_task() -> None:
    chat_mod._active_turns.clear()
    fake_session = {
        "id": "ses_stop",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }
    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocking_sse(*_a, **_k):
        started.set()
        yield 'event: state\ndata: {"busy":true}\n\n'
        await release.wait()
        yield 'event: done\ndata: {}\n\n'

    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod.store, "try_begin_session_turn", return_value=True),
        patch.object(chat_mod.store, "end_session_turn"),
        patch.object(chat_mod, "stream_turn_sse", _blocking_sse),
    ):
        turn, q = await chat_mod.start_session_turn("ses_stop", "hi", "web")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert get_active_session_turn("ses_stop") is turn

        assert chat_mod.cancel_session_turn("ses_stop") is True
        if turn.task:
            with pytest.raises(asyncio.CancelledError):
                await turn.task

        # Sentinel still delivered so SSE drains exit.
        got_none = False
        while not q.empty():
            if q.get_nowait() is None:
                got_none = True
        assert got_none
        assert get_active_session_turn("ses_stop") is None

    assert chat_mod.cancel_session_turn("ses_missing") is False
    chat_mod._active_turns.clear()


@pytest.mark.asyncio
async def test_start_session_turn_finally_keeps_newer_registry_slot() -> None:
    """A finished runner must not pop a newer turn that replaced its registry slot."""
    chat_mod._active_turns.clear()
    fake_session = {
        "id": "ses_x",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }
    release = asyncio.Event()

    async def _blocking_sse(*_a, **_k):
        yield 'event: state\ndata: {"busy":true}\n\n'
        await release.wait()

    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod, "stream_turn_sse", _blocking_sse),
    ):
        old, _ = await chat_mod.start_session_turn("ses_x", "one", "web")
        assert chat_mod._active_turns.get("ses_x") is old

        # Force-replace registry as a racing second start would (after checks).
        newer = _ActiveTurn(session_id="ses_x")

        async def _noop() -> None:
            return None

        newer.task = asyncio.create_task(_noop())
        chat_mod._active_turns["ses_x"] = newer

        release.set()
        if old.task:
            await old.task

        assert chat_mod._active_turns.get("ses_x") is newer
        await newer.task

    chat_mod._active_turns.clear()

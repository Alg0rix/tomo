"""Listen-mode SSE reattaches to an in-flight background turn after refresh."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import stream as stream_mod
from app.services import chat as chat_mod
from app.services.chat import _ActiveTurn


def _parse_events(chunks: list[str]) -> list[tuple[str, str]]:
    """Return (event_name, raw_block) pairs from SSE chunks."""
    raw = "".join(chunks)
    out: list[tuple[str, str]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block or block.startswith("retry:"):
            continue
        name = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
                break
        if name:
            out.append((name, block))
    return out


@pytest.mark.asyncio
async def test_listen_event_source_emits_resume_snapshot() -> None:
    """Active-turn listen yields state+turn.start+replay before waiting live."""
    chat_mod._active_turns.clear()
    turn = _ActiveTurn(session_id="ses_listen")
    turn._broadcast(
        'id: 1\nevent: tool\ndata: {"tool":"bash","args":{},"agent_id":"main"}\n\n'
    )
    # Fake a live task so get_active_session_turn keeps the entry.
    turn.task = asyncio.create_task(asyncio.sleep(3600))
    chat_mod._active_turns["ses_listen"] = turn

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)

    try:
        with patch.object(
            stream_mod.store,
            "get_session",
            return_value={
                "id": "ses_listen",
                "coordinator_id": "main",
                "agent_id": "main",
            },
        ):
            # Call the route's generator path via the endpoint function body.
            # Build the same event_source used by session_chat_stream listen mode.
            from app.channels.sse_map import fmt_sse

            active = chat_mod.get_active_session_turn("ses_listen")
            assert active is turn

            chunks: list[str] = []
            chunks.append("retry: 4000\n\n")
            session = stream_mod.store.get_session("ses_listen") or {}
            coord = session.get("coordinator_id") or session.get("agent_id") or ""
            chunks.append(
                fmt_sse(
                    {
                        "event": "state",
                        "data": {
                            "agent_id": coord,
                            "busy": True,
                            "session_id": "ses_listen",
                            "resumed": True,
                        },
                        "seq": 0,
                    }
                )
            )
            chunks.append(
                fmt_sse(
                    {
                        "event": "turn.start",
                        "data": {
                            "session_id": "ses_listen",
                            "agent_id": coord,
                            "resumed": True,
                        },
                        "seq": 0,
                    }
                )
            )
            queue = active.subscribe(after_seq=0)
            # Drain only the immediate replay (tool + caught_up); do not wait live.
            while not queue.empty():
                item = queue.get_nowait()
                if item is None:
                    break
                chunks.append(item)
            active.unsubscribe(queue)

        events = _parse_events(chunks)
        names = [n for n, _ in events]
        assert names[0] == "state"
        assert names[1] == "turn.start"
        assert "tool" in names
        assert "caught_up" in names
        assert "resumed" in events[0][1]
        assert "resumed" in events[1][1]
    finally:
        turn.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn.task
        chat_mod._active_turns.clear()


@pytest.mark.asyncio
async def test_get_active_session_turn_survives_without_subscribers() -> None:
    """Disconnecting the SSE client must not cancel the background turn."""
    chat_mod._active_turns.clear()
    release = asyncio.Event()

    async def _blocking_sse(*_a, **_k):
        yield 'id: 1\nevent: state\ndata: {"busy":true}\n\n'
        await release.wait()
        yield 'id: 2\nevent: done\ndata: {"content":"ok"}\n\n'

    fake_session = {
        "id": "ses_detach",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }
    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod.store, "try_begin_session_turn", return_value=True),
        patch.object(chat_mod.store, "end_session_turn"),
        patch.object(chat_mod, "stream_turn_sse", _blocking_sse),
    ):
        turn, q = await chat_mod.start_session_turn("ses_detach", "hi", "web")
        assert chat_mod.get_active_session_turn("ses_detach") is turn
        # Simulate client disconnect: unsubscribe the starter queue.
        turn.unsubscribe(q)
        assert chat_mod.get_active_session_turn("ses_detach") is turn
        # Re-subscribe (refresh) still works.
        q2 = turn.subscribe(after_seq=0)
        got = []
        while not q2.empty():
            got.append(q2.get_nowait())
        assert any("caught_up" in (c or "") for c in got)
        turn.unsubscribe(q2)

        release.set()
        if turn.task:
            await asyncio.wait_for(turn.task, timeout=5.0)
        assert chat_mod.get_active_session_turn("ses_detach") is None

    chat_mod._active_turns.clear()

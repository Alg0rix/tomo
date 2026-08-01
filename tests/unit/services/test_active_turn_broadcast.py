"""Active-turn broadcast must always deliver the completion sentinel."""

from __future__ import annotations

import asyncio

import pytest

from app.services.chat import _ActiveTurn


@pytest.mark.asyncio
async def test_broadcast_none_delivered_when_queue_full() -> None:
    turn = _ActiveTurn(session_id="s1")
    q = turn.subscribe()
    # Fill to capacity with dummy chunks.
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

"""Mid-turn session steer inbox."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import chat as chat_mod
from app.services.chat import (
    cancel_session_turn,
    drain_session_steers,
    get_active_session_turn,
    push_session_steer,
)


@pytest.mark.asyncio
async def test_push_steer_rejected_without_active_turn() -> None:
    chat_mod._active_turns.clear()
    result = push_session_steer("ses_none", "hello")
    assert result["ok"] is False
    assert result["accepted"] is False
    assert result["reason"] == "no_active_turn"


@pytest.mark.asyncio
async def test_push_and_drain_steer_mid_turn() -> None:
    chat_mod._active_turns.clear()
    fake_session = {
        "id": "ses_steer",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }

    async def _empty_sse(*_a, **_k):
        # Keep the turn alive until we cancel.
        await asyncio.sleep(60)
        if False:  # pragma: no cover
            yield ""
        return

    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod.store, "try_begin_session_turn", return_value=True),
        patch.object(chat_mod.store, "end_session_turn"),
        patch.object(chat_mod, "stream_turn_sse", _empty_sse),
    ):
        turn, _q = await chat_mod.start_session_turn("ses_steer", "hi", "web")
        assert get_active_session_turn("ses_steer") is turn

        rejected = push_session_steer("ses_steer", "")
        assert rejected["accepted"] is False

        ok = push_session_steer("ses_steer", "also do X", attachment_ids=["att1"])
        assert ok["accepted"] is True
        assert ok["pending"] == 1

        items = drain_session_steers("ses_steer")
        assert len(items) == 1
        assert items[0]["content"] == "also do X"
        assert items[0]["attachment_ids"] == ["att1"]
        assert drain_session_steers("ses_steer") == []

        turn.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn.task
        # Runner finally clears registry
        await asyncio.sleep(0)
        chat_mod._active_turns.pop("ses_steer", None)


@pytest.mark.asyncio
async def test_cancel_clears_steer_inbox() -> None:
    chat_mod._active_turns.clear()
    fake_session = {
        "id": "ses_cancel_steer",
        "coordinator_id": "main",
        "agent_id": "main",
        "agent_ids": ["main"],
    }

    async def _empty_sse(*_a, **_k):
        await asyncio.sleep(60)
        if False:  # pragma: no cover
            yield ""
        return

    with (
        patch.object(chat_mod.store, "get_session", return_value=fake_session),
        patch.object(chat_mod.store, "try_begin_session_turn", return_value=True),
        patch.object(chat_mod.store, "end_session_turn"),
        patch.object(chat_mod, "stream_turn_sse", _empty_sse),
    ):
        turn, _q = await chat_mod.start_session_turn("ses_cancel_steer", "hi", "web")
        assert push_session_steer("ses_cancel_steer", "pending")["accepted"]
        assert cancel_session_turn("ses_cancel_steer") is True
        assert drain_session_steers("ses_cancel_steer") == []
        turn.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn.task
        chat_mod._active_turns.clear()

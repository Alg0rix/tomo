"""Scheduler runner fires a session turn (ScriptedLLM) — Alpha Slice G."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app.scheduler import fire_due_schedules, fire_schedule
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply


@pytest.fixture(autouse=True)
def _inject_scripted_llm(monkeypatch) -> None:
    client = ScriptedLLM([text_reply("Scheduled reply.")] * 10)
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None, **kwargs: client,
    )


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "scheduler.db")


async def test_fire_schedule_runs_turn_and_logs(tmp_path: Path) -> None:
    from tests.fakes.access import owned_host_session
    _rebind(tmp_path)
    sid = owned_host_session()
    context = store.access.resolve_context("usr_admin", sid)
    sch = store.access.create_schedule_for_context(
        context,
        {
            "id": "sch_fire",
            "name": "Fire test",
            "agent_id": "main",
            "interval_seconds": 60,
            "message": "hello from schedule",
            "enabled": True,
            "next_run": time.time() - 1,
        },
    )
    result = await fire_schedule(sch)
    assert result["status"] == "ok"
    assert result["session_id"]

    runs = store.list_schedule_runs("sch_fire")
    assert len(runs) >= 1
    assert runs[0]["status"] == "ok"

    history = store.get_session_history(result["session_id"])
    assert any(
        e.get("type") == "user" and "hello from schedule" in (e.get("content") or "")
        for e in history
    )

    updated = store.get_schedule("sch_fire")
    assert updated is not None
    assert updated["last_run"] is not None
    assert updated["next_run"] is not None
    assert updated["next_run"] > updated["last_run"]


class PausedReasoningProvider(ScriptedLLM):
    """External provider seam; pause before reasoning is persisted."""
    def __init__(self):
        super().__init__([text_reply('Scheduled result')] * 10)
        self.started = asyncio.Event()
        self.next_piece = asyncio.Event()
        self.finish = asyncio.Event()
        self.stream_calls = 0

    async def stream_complete(self, messages, tools=None):
        self.stream_calls += 1
        yield {'type': 'reasoning_delta', 'content': 'First reasoning token'}
        self.started.set()
        await self.next_piece.wait()
        yield {'type': 'reasoning_delta', 'content': ' second token'}
        await self.finish.wait()
        yield {'type': 'delta', 'content': 'Scheduled result'}
        response = text_reply('Scheduled result')
        response.reasoning = 'First reasoning token second token'
        yield {'type': 'done', 'response': response}


def scheduled_reasoning(tmp_path, monkeypatch):
    from tests.fakes.access import owned_host_session
    _rebind(tmp_path)
    store.update_settings({'learning_enabled': False})
    source = owned_host_session()
    context = store.access.resolve_context('usr_admin', source)
    schedule = store.access.create_schedule_for_context(context, {
        'name': 'Live reasoning check', 'schedule': 'every 1h',
        'agent_id': 'main', 'message': 'Perform scheduled work',
    })
    provider = PausedReasoningProvider()
    monkeypatch.setattr('app.runtime.agent.loop.get_llm', lambda agent_id=None, **kwargs: provider)
    return schedule, provider


async def test_scheduler_reasoning_is_live_replayable_and_finishes_ui_subscription(tmp_path, monkeypatch):
    from app.services.chat import get_active_session_turn
    schedule, provider = scheduled_reasoning(tmp_path, monkeypatch)
    task = asyncio.create_task(fire_schedule(schedule, skip_claim=True))
    active, queue, rejoined = None, None, None
    try:
        await asyncio.wait_for(provider.started.wait(), 5)
        run = store.list_schedule_runs(schedule['id'])[0]
        sid = run['session_id']
        active = get_active_session_turn(sid)
        assert active is not None, 'scheduler must publish through the UI live-turn manager'
        assert active.request['origin'] == 'scheduler'
        assert not any(e['type'] in ('thinking', 'final') for e in store.get_session_history(sid))
        queue = active.subscribe()
        async def thinking(subscription):
            while True:
                chunk = await subscription.get()
                assert chunk is not None
                if 'event: thinking_delta\n' in chunk:
                    return chunk
        first = await asyncio.wait_for(thinking(queue), 5)
        assert 'First reasoning token' in first
        seq = next(int(line[4:]) for line in first.splitlines() if line.startswith('id: '))
        active.unsubscribe(queue)
        queue = None
        rejoined = active.subscribe(after_seq=seq)
        provider.next_piece.set()
        second = await asyncio.wait_for(thinking(rejoined), 5)
        assert 'second token' in second and 'First reasoning token' not in second
        assert provider.stream_calls == 1
        assert not any(e['type'] == 'thinking' for e in store.get_session_history(sid))
        provider.finish.set()
        async def terminal():
            frames = []
            while True:
                chunk = await rejoined.get()
                if chunk is None:
                    return frames
                frames.append(chunk)
        frames = await asyncio.wait_for(terminal(), 10)
        assert any('event: done\n' in frame for frame in frames)
        states = [json.loads(line[6:]) for frame in frames if 'event: state\n' in frame
                  for line in frame.splitlines() if line.startswith('data: ')]
        assert states[-1]['busy'] is False
        result = await asyncio.wait_for(task, 5)
        assert result['status'] == 'ok'
        assert get_active_session_turn(sid) is None and not store.is_session_turn_active(sid)
        assert len(store.list_schedule_runs(schedule['id'])) == 1 and provider.stream_calls == 1
    finally:
        provider.next_piece.set()
        provider.finish.set()
        if active:
            for subscription in (queue, rejoined):
                if subscription is not None:
                    active.unsubscribe(subscription)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('shutdown', [False, True], ids=['stop', 'restart'])
async def test_scheduler_stop_and_restart_never_replay_business_work(tmp_path, monkeypatch, shutdown):
    from app.services.chat import (cancel_session_turn, get_active_session_turn,
                                   recover_web_turns, suspend_session_turns)
    from app.services import turn_recovery
    schedule, provider = scheduled_reasoning(tmp_path, monkeypatch)
    task = asyncio.create_task(fire_schedule(schedule, skip_claim=True))
    try:
        await asyncio.wait_for(provider.started.wait(), 5)
        sid = store.list_schedule_runs(schedule['id'])[0]['session_id']
        assert get_active_session_turn(sid) is not None
        if shutdown:
            await suspend_session_turns()
            assert any(r.get('origin') == 'scheduler' for r in turn_recovery.pending_requests())
        else:
            assert cancel_session_turn(sid)
        result = await asyncio.wait_for(task, 5)
        assert result['status'] == 'error'
        await recover_web_turns()
        assert provider.stream_calls == 1
        assert get_active_session_turn(sid) is None and not store.is_session_turn_active(sid)
        assert not turn_recovery.pending_requests()
        assert not any(e['type'] == 'final' for e in store.get_session_history(sid))
    finally:
        provider.next_piece.set()
        provider.finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await recover_web_turns()


async def test_fire_due_schedules_skips_disabled(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_schedule(
        {
            "id": "sch_off",
            "name": "Off",
            "agent_id": "main",
            "interval_seconds": 30,
            "enabled": False,
            "next_run": time.time() - 10,
        }
    )
    store.create_schedule(
        {
            "id": "sch_on",
            "name": "On",
            "agent_id": "ops",
            "interval_seconds": 30,
            "message": "due ping",
            "enabled": True,
            "next_run": time.time() - 10,
        }
    )
    results = await fire_due_schedules(now=time.time())
    ids = {r["schedule_id"] for r in results}
    assert "sch_on" in ids
    assert "sch_off" not in ids

"""Scheduler runner fires a session turn (ScriptedLLM) — Alpha Slice G."""

from __future__ import annotations

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
        lambda agent_id=None: client,
    )


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "scheduler.db")


async def test_fire_schedule_runs_turn_and_logs(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sch = store.create_schedule(
        {
            "id": "sch_fire",
            "name": "Fire test",
            "agent_id": "main",
            "interval_seconds": 60,
            "message": "hello from schedule",
            "enabled": True,
            "next_run": time.time() - 1,
        }
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

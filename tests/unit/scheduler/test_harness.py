"""Schedule harness — claim, pause/resume, one-shot, agent tool."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.runtime.tools import schedule as schedule_tool
from app.runtime.tools.sandbox import bind_agent, reset_agent
from app.scheduler import fire_due_schedules, fire_schedule, parse_schedule
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply


@pytest.fixture(autouse=True)
def _inject_scripted_llm(monkeypatch) -> None:
    client = ScriptedLLM([text_reply("Scheduled reply.")] * 20)
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: client,
    )


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "scheduler_harness.db")


def test_create_with_schedule_string(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sch = store.create_schedule(
        {
            "name": "Hourly",
            "agent_id": "main",
            "schedule": "every 1h",
            "message": "ping",
        }
    )
    assert sch["schedule_kind"] == "interval"
    assert sch["interval_seconds"] == 3600
    assert sch["next_run"] is not None
    assert sch["state"] == "scheduled"


def test_create_cron_and_oneshot(tmp_path: Path) -> None:
    _rebind(tmp_path)
    cron = store.create_schedule(
        {
            "name": "Morning",
            "agent_id": "main",
            "schedule": "0 9 * * *",
            "message": "digest",
        }
    )
    assert cron["schedule_kind"] == "cron"
    assert cron["schedule_expr"] == "0 9 * * *"
    assert cron["next_run"] is not None

    once = store.create_schedule(
        {
            "name": "Soon",
            "agent_id": "ops",
            "schedule": "5m",
            "message": "remind me",
        }
    )
    assert once["schedule_kind"] == "once"
    assert once["repeat_times"] == 1


def test_pause_resume(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sch = store.create_schedule(
        {
            "name": "P",
            "agent_id": "main",
            "schedule": "every 30m",
            "message": "x",
        }
    )
    paused = store.pause_schedule(sch["id"], reason="hold")
    assert paused is not None
    assert paused["enabled"] is False
    assert paused["state"] == "paused"
    assert paused["next_run"] is None
    assert paused["pause_reason"] == "hold"

    resumed = store.resume_schedule(sch["id"])
    assert resumed is not None
    assert resumed["enabled"] is True
    assert resumed["state"] == "scheduled"
    assert resumed["next_run"] is not None


def test_claim_prevents_double_fire(tmp_path: Path) -> None:
    _rebind(tmp_path)
    now = time.time()
    sch = store.create_schedule(
        {
            "id": "sch_claim",
            "name": "Claim",
            "agent_id": "main",
            "schedule": "every 1h",
            "message": "x",
            "next_run": now - 1,
        }
    )
    a = store.claim_schedule_for_fire(sch["id"], now=now)
    b = store.claim_schedule_for_fire(sch["id"], now=now)
    assert a is not None
    assert b is None


async def test_oneshot_completes_after_fire(tmp_path: Path) -> None:
    _rebind(tmp_path)
    now = time.time()
    store.create_schedule(
        {
            "id": "sch_once",
            "name": "Once",
            "agent_id": "main",
            "schedule": "1m",
            "message": "one shot body",
            "next_run": now - 1,
        }
    )
    # Force due
    store.update_schedule("sch_once", {"next_run": now - 1, "enabled": True})
    result = await fire_schedule(store.get_schedule("sch_once"), now=now)
    assert result["status"] == "ok"
    done = store.get_schedule("sch_once")
    assert done is not None
    assert done["state"] == "completed"
    assert done["enabled"] is False
    # Not due again
    due = await fire_due_schedules(now=time.time() + 10)
    assert all(r["schedule_id"] != "sch_once" for r in due)


def test_tool_create_list_pause(tmp_path: Path) -> None:
    _rebind(tmp_path)
    token = None
    token = bind_agent("main")
    try:
        out = json.loads(
            schedule_tool.run(
                {
                    "action": "create",
                    "schedule": "every 15m",
                    "message": "health check the stack",
                    "name": "Health",
                }
            )
        )
        assert out["success"] is True
        sid = out["schedule_id"]

        listed = json.loads(schedule_tool.run({"action": "list"}))
        assert listed["success"] is True
        assert any(j["id"] == sid for j in listed["jobs"])

        paused = json.loads(
            schedule_tool.run(
                {"action": "pause", "schedule_id": sid, "reason": "maintenance"}
            )
        )
        assert paused["success"] is True
        assert paused["job"]["state"] == "paused"

        empty = json.loads(schedule_tool.run({"action": "list"}))
        assert all(j["id"] != sid for j in empty["jobs"])

        with_disabled = json.loads(
            schedule_tool.run({"action": "list", "include_disabled": True})
        )
        assert any(j["id"] == sid for j in with_disabled["jobs"])
    finally:
        if token is not None:
            reset_agent(token)


def test_tool_run_now(tmp_path: Path) -> None:
    _rebind(tmp_path)
    token = None
    token = bind_agent("main")
    try:
        created = json.loads(
            schedule_tool.run(
                {
                    "action": "create",
                    "schedule": "every 1h",
                    "message": "manual fire please",
                    "name": "Manual",
                }
            )
        )
        sid = created["schedule_id"]
        # Push next_run into the future so due tick would skip it
        store.update_schedule(sid, {"next_run": time.time() + 3600})
        ran = json.loads(
            schedule_tool.run({"action": "run", "schedule_id": sid})
        )
        assert ran["success"] is True
        assert ran["execution"]["status"] == "ok"
        runs = store.list_schedule_runs(sid)
        assert len(runs) >= 1
        assert ran["execution"].get("claimed") is False
    finally:
        if token is not None:
            reset_agent(token)


def test_legacy_interval_seconds_still_works(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sch = store.create_schedule(
        {
            "name": "Legacy",
            "agent_id": "main",
            "interval_seconds": 120,
            "message": "legacy",
        }
    )
    assert sch["interval_seconds"] == 120
    assert sch["schedule_kind"] == "interval"


def test_parse_roundtrip_display() -> None:
    for s in ("every 30m", "0 9 * * *", "2h"):
        p = parse_schedule(s)
        assert p["display"]
        assert p["kind"] in ("interval", "cron", "once")

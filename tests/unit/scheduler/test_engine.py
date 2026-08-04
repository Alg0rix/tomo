"""APScheduler wake engine — register / remove / trigger mapping."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.scheduler import engine as eng
from app.services import store


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path):
    store.rebind(tmp_path / "aps.db")
    # Reset engine globals between tests
    eng._scheduler = None
    eng._started = False
    eng._loop = None
    yield
    eng._scheduler = None
    eng._started = False
    eng._loop = None


def test_build_trigger_interval():
    sch = {
        "id": "s1",
        "schedule_kind": "interval",
        "interval_seconds": 120,
        "next_run": time.time() + 60,
        "enabled": True,
        "state": "scheduled",
    }
    t = eng._build_trigger(sch)
    assert t is not None
    assert t.__class__.__name__ == "IntervalTrigger"


def test_build_trigger_cron():
    sch = {
        "id": "s2",
        "schedule_kind": "cron",
        "schedule_expr": "0 9 * * *",
        "cron": "0 9 * * *",
        "enabled": True,
        "state": "scheduled",
    }
    t = eng._build_trigger(sch)
    assert t is not None
    assert t.__class__.__name__ == "CronTrigger"


def test_build_trigger_once():
    sch = {
        "id": "s3",
        "schedule_kind": "once",
        "next_run": time.time() + 300,
        "schedule_expr": "",
        "enabled": True,
        "state": "scheduled",
    }
    t = eng._build_trigger(sch)
    assert t is not None
    assert t.__class__.__name__ == "DateTrigger"


def test_build_trigger_once_expired():
    sch = {
        "id": "s4",
        "schedule_kind": "once",
        "next_run": time.time() - 10_000,
        "enabled": True,
        "state": "scheduled",
    }
    assert eng._build_trigger(sch) is None


def test_sync_and_remove_with_mock_aps():
    sch = store.create_schedule(
        {
            "id": "sch_aps",
            "name": "APS",
            "agent_id": "main",
            "schedule": "every 1h",
            "message": "ping",
            "enabled": True,
        }
    )
    mock_aps = MagicMock()
    mock_aps.running = True
    mock_job = MagicMock()
    mock_job.next_run_time = None
    mock_aps.get_job.return_value = mock_job

    with patch.object(eng, "_scheduler", mock_aps), patch.object(
        eng, "_started", True
    ):
        assert eng.sync_schedule(sch["id"]) is True
        assert mock_aps.add_job.called
        eng.remove_schedule(sch["id"])
        mock_aps.remove_job.assert_called_with(sch["id"])


def test_sync_skips_paused():
    sch = store.create_schedule(
        {
            "id": "sch_paused",
            "name": "P",
            "agent_id": "main",
            "schedule": "every 30m",
            "message": "x",
            "enabled": True,
        }
    )
    store.pause_schedule(sch["id"])
    mock_aps = MagicMock()
    mock_aps.running = True
    with patch.object(eng, "_scheduler", mock_aps), patch.object(
        eng, "_started", True
    ):
        assert eng.sync_schedule(sch["id"]) is False
        mock_aps.add_job.assert_not_called()

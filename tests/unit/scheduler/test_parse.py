"""Schedule parser — duration / every / cron / ISO."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from app.scheduler.parse import (
    ONESHOT_GRACE_SECONDS,
    compute_next_run,
    parse_duration,
    parse_schedule,
)


def test_parse_duration_units() -> None:
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("1d") == 86400
    assert parse_duration("90s") == 90
    assert parse_duration("1h30m") == 5400


@pytest.mark.parametrize("bad", ["", "0", "-30", "30x", "abc", "1h30x"])
def test_parse_duration_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_parse_every_interval() -> None:
    p = parse_schedule("every 2h")
    assert p["kind"] == "interval"
    assert p["interval_seconds"] == 7200
    assert "2h" in p["display"]


def test_parse_bare_seconds_interval() -> None:
    p = parse_schedule("3600")
    assert p["kind"] == "interval"
    assert p["interval_seconds"] == 3600


def test_parse_duration_oneshot() -> None:
    now = time.time()
    p = parse_schedule("30m", now=now)
    assert p["kind"] == "once"
    assert p["run_at"] == pytest.approx(now + 1800, abs=1)


def test_parse_iso_oneshot() -> None:
    p = parse_schedule("2030-06-01T09:00:00")
    assert p["kind"] == "once"
    expected = datetime(2030, 6, 1, 9, 0).timestamp()
    assert p["run_at"] == pytest.approx(expected, abs=1)
    assert "once at" in p["display"]


def test_parse_cron() -> None:
    p = parse_schedule("0 9 * * *")
    assert p["kind"] == "cron"
    assert p["expr"] == "0 9 * * *"


def test_parse_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid schedule"):
        parse_schedule("not a schedule")


def test_compute_next_interval() -> None:
    parsed = parse_schedule("every 10m")
    now = 1_700_000_000.0
    nxt = compute_next_run(parsed, now=now)
    assert nxt == now + 600
    nxt2 = compute_next_run(parsed, now=now, last_run=now)
    assert nxt2 == now + 600


def test_compute_next_oneshot_grace() -> None:
    now = time.time()
    parsed = {
        "kind": "once",
        "run_at": now - 30,
        "interval_seconds": 0,
        "expr": "",
        "display": "once",
    }
    assert compute_next_run(parsed, now=now) == pytest.approx(now - 30, abs=0.1)
    # Far past grace → None
    parsed["run_at"] = now - ONESHOT_GRACE_SECONDS - 10
    assert compute_next_run(parsed, now=now) is None
    # Already ran
    parsed["run_at"] = now + 60
    assert compute_next_run(parsed, now=now, last_run=now) is None


def test_compute_next_cron_daily() -> None:
    # Pick a fixed local morning and ensure next 09:00 is tomorrow or today
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    after = base.timestamp()
    nxt = compute_next_run(
        {"kind": "cron", "expr": "0 9 * * *", "interval_seconds": 0},
        now=after,
    )
    assert nxt is not None
    dt = datetime.fromtimestamp(nxt)
    assert dt.hour == 9
    assert dt.minute == 0
    assert nxt > after
    # Should be within ~25h
    assert nxt - after < 26 * 3600


def test_cron_every_5_minutes() -> None:
    now = datetime.now().replace(second=0, microsecond=0)
    # Align to a known minute
    after = now.timestamp()
    nxt = compute_next_run(
        {"kind": "cron", "expr": "*/5 * * * *", "interval_seconds": 0},
        now=after,
    )
    assert nxt is not None
    dt = datetime.fromtimestamp(nxt)
    assert dt.minute % 5 == 0
    assert nxt > after
    assert nxt - after <= 5 * 60 + 1

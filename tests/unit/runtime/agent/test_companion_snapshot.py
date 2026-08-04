"""Companion aggregates — honest message-day signals."""

from __future__ import annotations

import time

import pytest

from app.runtime.agent.learning.companion import (
    count_user_messages,
    distinct_active_days,
    first_activity_at,
)
from app.services import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    original = getattr(store, "_path", None)
    store.rebind(tmp_path / "comp.db")
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    yield store
    if original is not None:
        try:
            store.rebind(original)
        except Exception:
            pass


def test_companion_snapshot_shape(db) -> None:
    snap = db.companion_snapshot()
    assert 0 <= snap["bond"] <= 100
    assert set(snap["bond_parts"]) == {
        "chats",
        "saved_events",
        "user_memory_chars",
        "library_skills",
        "days_active",
    }
    assert "growth" in snap
    assert "recent_events" in snap
    assert "user_profile_preview" in snap


def test_active_days_from_user_messages(db) -> None:
    if not db.list_agents():
        pytest.skip("no agents seeded")
    try:
        session = db.create_home_session("web")
    except Exception:
        agents = db.list_agents()
        session = db.create_session(agents[0]["id"], "web")
    sid = session.get("session_id") or session.get("id")
    assert sid

    day1 = time.time() - 86400 * 3
    day2 = time.time() - 86400
    db.append_session_history(sid, {"type": "user", "content": "hello day1", "ts": day1})
    db.append_session_history(sid, {"type": "user", "content": "hello day2", "ts": day2})
    db.insert_learning_event(saved=True, created_at=day2, diary="lesson")

    def _check(conn):
        assert count_user_messages(conn) >= 2
        assert distinct_active_days(conn) >= 2
        first = first_activity_at(conn)
        assert first is not None
        assert first <= day1 + 1

    db.with_db(_check)

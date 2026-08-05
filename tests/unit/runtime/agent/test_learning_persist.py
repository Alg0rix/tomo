"""Persist / hydrate learning counters across process restart."""

from __future__ import annotations

import pytest

from app.runtime.agent.learning.state import (
    begin_review,
    finish_review,
    get_state,
    observe_turn,
    reset_learning_state,
    snapshot,
)
from app.services import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    original = getattr(store, "_path", None)
    reset_learning_state()
    store.rebind(tmp_path / "learn_persist.db")
    store.update_settings(
        {
            "learning_enabled": True,
            "learning_memory_nudge_turns": 2,
            "learning_skill_nudge_iters": 99,
            "learning_cooldown_sec": 0,
        }
    )
    yield store
    reset_learning_state()
    if original is not None:
        try:
            store.rebind(original)
        except Exception:
            pass


def test_sticky_dues_survive_reset_via_sqlite(db) -> None:
    observe_turn(agent_id="p1", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="p1", tool_calls=0, ended_kind="final")
    assert plan is not None and plan.review_memory

    # Simulate process restart: clear in-memory maps, rehydrate from SQLite.
    reset_learning_state()
    st = get_state("p1")
    assert st.memory_due is True
    assert st.turns_since_memory >= 2


def test_review_counters_persist(db) -> None:
    observe_turn(agent_id="p2", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="p2", tool_calls=0, ended_kind="final")
    assert plan is not None
    assert begin_review(plan) is True
    finish_review("p2", saved=True)

    reset_learning_state()
    snap = snapshot("p2")
    assert snap["reviews_started"] >= 1
    assert snap["reviews_saved"] >= 1
    assert snap["memory_due"] is False

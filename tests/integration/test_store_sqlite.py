"""Integration: store round-trip over a temp SQLite DB.

Exercises the hybrid facade end-to-end against a per-test temp database:
seeded agents are visible, a session can be created, user + final messages
appended, history listed back, and settings persist across a rebind to the
same DB file.
"""

from __future__ import annotations

import time

from app.services import store


def test_create_session_append_and_list_history(tmp_path) -> None:
    store.rebind(tmp_path / "integration.db")

    # seeded agents are available through the SQLite-backed facade
    agents = store.list_agents()
    assert {a["id"] for a in agents} >= {"main", "ops", "research", "support"}

    sid = store.create_swarm_session(["main", "research"], user_id="web")
    store.append_session_history(sid, {"type": "user", "content": "What is 2+2?", "ts": time.time()})
    store.append_session_history(
        sid, {"type": "final", "content": "4", "agent_id": "main", "ts": time.time()}
    )

    history = store.get_session_history(sid)
    assert len(history) == 2
    assert history[0]["type"] == "user"
    assert history[1]["type"] == "final"
    assert history[1]["content"] == "4"

    session = store.get_session(sid)
    assert session is not None
    assert session["message_count"] == 2
    assert session["agent_ids"] == ["main", "research"]
    assert session["coordinator_id"] == "main"


def test_settings_persist_across_rebind(tmp_path) -> None:
    db = tmp_path / "settings.db"
    store.rebind(db)
    assert store.get_settings()["setup_complete"] is True
    updated = store.update_settings({"theme": "light", "max_tool_iterations": 6})
    assert updated["theme"] == "light"
    assert updated["max_tool_iterations"] == 6

    # reopen the same file: seed_if_empty skips (table non-empty), edits persist
    store.rebind(db)
    persisted = store.get_settings()
    assert persisted["theme"] == "light"
    assert persisted["max_tool_iterations"] == 6

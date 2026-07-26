"""Session + message history CRUD tests over the SQLite-backed store."""

from __future__ import annotations

import time

import pytest

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "sm.db")


def test_seeded_sessions_present(tmp_path) -> None:
    _rebind(tmp_path)
    sessions = {s["id"]: s for s in store.list_sessions()}
    assert "ses_001" in sessions
    assert sessions["ses_001"]["agent_ids"] == ["main", "ops", "research"]
    assert sessions["ses_001"]["coordinator_id"] == "main"


def test_create_swarm_session_picks_super_coordinator(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main", "ops"], user_id="web")
    s = store.get_session(sid)
    assert s is not None
    assert s["agent_ids"] == ["main", "ops"]
    assert s["coordinator_id"] == "main"
    assert s["agent_id"] == "main"
    assert s["title"] == "New swarm chat"


def test_create_swarm_session_requires_valid_agent(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_swarm_session(["ghost"])


def test_get_session_missing(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.get_session("nope") is None


def test_append_and_list_history(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(sid, {"type": "user", "content": "hello", "ts": time.time()})
    store.append_session_history(
        sid, {"type": "final", "content": "hi there", "agent_id": "main", "ts": time.time()}
    )
    history = store.get_session_history(sid)
    assert [e["type"] for e in history] == ["user", "final"]
    assert history[0]["content"] == "hello"
    assert store.get_session(sid)["message_count"] == 2


def test_first_user_message_renames_session(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(
        sid, {"type": "user", "content": "Plan the Q3 launch carefully", "ts": time.time()}
    )
    assert store.get_session(sid)["title"] == "Plan the Q3 launch carefully"


def test_derive_session_title_collapses_and_truncates() -> None:
    from app.models.mixins.messages import derive_session_title

    assert derive_session_title("  hello\nworld  ") == "hello world"
    long = "word " * 30
    title = derive_session_title(long, max_len=40)
    assert len(title) <= 41  # ellipsis
    assert title.endswith("…")
    assert "  " not in title


def test_append_returns_resolved_title_once(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    first = store.append_session_history(
        sid, {"type": "user", "content": "Ship the darkroom fix tonight", "ts": time.time()}
    )
    assert first == "Ship the darkroom fix tonight"
    second = store.append_session_history(
        sid, {"type": "user", "content": "and also the docs", "ts": time.time()}
    )
    assert second is None
    assert store.get_session(sid)["title"] == "Ship the darkroom fix tonight"


def test_set_session_title(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    before = store.get_session(sid)["updated_at"]
    s = store.set_session_title(sid, "Q3 Launch Plan")
    assert s is not None
    assert s["title"] == "Q3 Launch Plan"
    assert store.get_session(sid)["title"] == "Q3 Launch Plan"
    assert store.get_session(sid)["updated_at"] >= before
    assert store.set_session_title("ses_missing", "Nope") is None


def test_clear_session_by_id(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(sid, {"type": "user", "content": "x"})
    store.clear_session_by_id(sid)
    assert store.get_session_history(sid) == []
    assert store.get_session(sid)["message_count"] == 0


def test_update_session_agents(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    s = store.update_session_agents(sid, ["main", "research"])
    assert s is not None
    assert s["agent_ids"] == ["main", "research"]
    with pytest.raises(ValueError):
        store.update_session_agents(sid, ["ghost"])


def test_update_session_agents_missing_returns_none(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.update_session_agents("nope", ["main"]) is None


def test_get_or_create_session_idempotent(tmp_path) -> None:
    _rebind(tmp_path)
    sid1 = store.get_or_create_session("ops", "web")
    sid2 = store.get_or_create_session("ops", "web")
    assert sid1 == sid2
    s = store.get_session(sid1)
    assert s["agent_ids"] == ["ops"]
    assert s["coordinator_id"] == "ops"


def test_legacy_append_get_clear_history(tmp_path) -> None:
    _rebind(tmp_path)
    store.append_history("research", "web", {"type": "user", "content": "summarize", "ts": time.time()})
    store.append_history(
        "research", "web", {"type": "final", "content": "done", "agent_id": "research", "ts": time.time()}
    )
    history = store.get_history("research", "web")
    assert [e["type"] for e in history] == ["user", "final"]
    store.clear_session("research", "web")
    assert store.get_history("research", "web") == []


def test_delete_agent_removes_from_session_membership(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main", "ops"])
    store.delete_agent("ops")
    s = store.get_session(sid)
    assert s is not None
    assert s["agent_ids"] == ["main"]
    assert s["coordinator_id"] == "main"


def test_delete_agent_drops_solo_session(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["ops"])
    store.delete_agent("ops")
    assert store.get_session(sid) is None


def test_get_or_create_session_missing_agent_raises(tmp_path) -> None:
    """get_or_create_session must validate the agent (ValueError, not raw FK)."""
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.get_or_create_session("ghost", "web")
    # No session was created for the missing agent.
    assert not any(s["coordinator_id"] == "ghost" for s in store.list_sessions())


def test_clear_session_noop_when_no_session(tmp_path) -> None:
    """clear_session(agent, user) must not invent an empty session (P3)."""
    _rebind(tmp_path)
    store.create_agent({"id": "custom", "name": "Custom"})
    before_ids = {s["id"] for s in store.list_sessions()}
    store.clear_session("custom", "web")  # no prior session -> no-op
    after_ids = {s["id"] for s in store.list_sessions()}
    assert before_ids == after_ids
    assert not any(s["coordinator_id"] == "custom" for s in store.list_sessions())


def test_clear_session_clears_existing_session(tmp_path) -> None:
    """When a session exists, clear_session clears messages but keeps the session."""
    _rebind(tmp_path)
    store.append_history("ops", "web", {"type": "user", "content": "hi", "ts": time.time()})
    store.append_history(
        "ops", "web", {"type": "final", "content": "hello", "agent_id": "ops", "ts": time.time()}
    )
    assert store.get_history("ops", "web") != []
    store.clear_session("ops", "web")
    assert store.get_history("ops", "web") == []
    # The session still exists (cleared, not deleted) — get_history reuses it.
    sessions = [s for s in store.list_sessions() if s["coordinator_id"] == "ops"]
    assert len(sessions) == 1
    assert sessions[0]["message_count"] == 0


"""Swarm sessions pick up newly enabled agents without re-editing membership."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "swarm_live.db")


def test_new_agent_joins_existing_swarm_session(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    before = set(store.get_session(sid)["agent_ids"])
    assert "main" in before
    assert "ops" in before

    store.create_agent({"id": "netops", "name": "NetOps", "role": "network"})
    after = set(store.get_session(sid)["agent_ids"])
    assert "netops" in after
    assert before.issubset(after)


def test_solo_session_does_not_auto_add_agents(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["ops"], user_id="web")
    assert store.get_session(sid)["agent_ids"] == ["ops"]
    assert store.get_session(sid).get("is_swarm") is False

    store.create_agent({"id": "netops", "name": "NetOps"})
    assert store.get_session(sid)["agent_ids"] == ["ops"]
    assert "netops" not in store.get_session(sid)["agent_ids"]


def test_reenabled_agent_rejoins_swarm(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    store.update_agent("research", {"enabled": False})
    # Disabled agents drop out of live resolve.
    assert "research" not in store.get_session(sid)["agent_ids"]

    store.update_agent("research", {"enabled": True})
    assert "research" in store.get_session(sid)["agent_ids"]


def test_swarm_api_label_not_agent_count(tmp_path: Path) -> None:
    """is_swarm true; agent_name is 'swarm' not a countable roster string."""
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    s = store.get_session(sid)
    assert s["is_swarm"] is True
    assert len(s["agent_ids"]) >= 2

"""Swarm sessions pick up newly enabled agents without re-editing membership."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "swarm_live.db")


def test_new_agent_does_not_join_existing_solo_session(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    before = set(store.get_session(sid)["agent_ids"])
    assert "main" in before
    assert "ops" not in before

    store.create_agent({"id": "netops", "name": "NetOps", "role": "network"})
    after = set(store.get_session(sid)["agent_ids"])
    assert "netops" not in after
    assert before == after


def test_solo_session_does_not_auto_add_agents(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["ops"], user_id="web")
    assert store.get_session(sid)["agent_ids"] == ["ops"]
    assert store.get_session(sid).get("is_swarm") is False

    store.create_agent({"id": "netops", "name": "NetOps"})
    assert store.get_session(sid)["agent_ids"] == ["ops"]
    assert "netops" not in store.get_session(sid)["agent_ids"]


def test_reenabled_agent_does_not_join_solo_session(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    store.update_agent("research", {"enabled": False})
    # Disabled agents drop out of live resolve.
    assert "research" not in store.get_session(sid)["agent_ids"]

    store.update_agent("research", {"enabled": True})
    assert "research" not in store.get_session(sid)["agent_ids"]


def test_solo_session_has_no_swarm_label(tmp_path: Path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    s = store.get_session(sid)
    assert s["is_swarm"] is False
    assert s["agent_ids"] == ["main"]

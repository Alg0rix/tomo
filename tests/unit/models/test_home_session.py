"""Home session + coordinator helper tests."""

from __future__ import annotations

import pytest

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "home.db")


def test_get_coordinator_prefers_super(tmp_path) -> None:
    _rebind(tmp_path)
    coord = store.get_coordinator()
    assert coord is not None
    assert coord["id"] == "main"
    assert coord["is_super"] is True
    assert coord["name"] == "Tomo"


def test_get_coordinator_falls_back_when_super_disabled(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_agent("main", {"enabled": False})
    coord = store.get_coordinator()
    assert coord is not None
    assert coord["id"] != "main"
    assert coord["enabled"] is True


def test_create_home_session_full_swarm(tmp_path) -> None:
    _rebind(tmp_path)
    created = store.create_home_session(user_id="web")
    assert created["coordinator_id"] == "main"
    assert created["coordinator_name"] == "Tomo"
    assert created["session_id"]

    session = store.get_session(created["session_id"])
    assert session is not None
    enabled = store.list_enabled_agent_ids()
    # Swarm includes every enabled agent; coordinator first.
    assert session["agent_ids"][0] == "main"
    assert set(session["agent_ids"]) == set(enabled)
    assert len(session["agent_ids"]) >= 2
    assert session["coordinator_id"] == "main"
    assert session["agent_id"] == "main"
    assert session["user_id"] == "web"


def test_create_swarm_empty_ids_means_all_enabled(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session([], user_id="web")
    session = store.get_session(sid)
    assert set(session["agent_ids"]) == set(store.list_enabled_agent_ids())


def test_create_home_session_raises_without_enabled_agents(tmp_path) -> None:
    _rebind(tmp_path)
    for a in store.list_agents():
        store.update_agent(a["id"], {"enabled": False})
    with pytest.raises(ValueError, match="coordinator"):
        store.create_home_session()

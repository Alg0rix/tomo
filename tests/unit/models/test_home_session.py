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


def test_create_home_session_coordinator_only(tmp_path) -> None:
    _rebind(tmp_path)
    created = store.create_home_session(user_id="web")
    assert created["coordinator_id"] == "main"
    assert created["coordinator_name"] == "Tomo"
    assert created["session_id"]

    session = store.get_session(created["session_id"])
    assert session is not None
    assert session["agent_ids"] == ["main"]
    assert session["coordinator_id"] == "main"
    assert session["agent_id"] == "main"
    assert session["user_id"] == "web"


def test_create_home_session_raises_without_enabled_agents(tmp_path) -> None:
    _rebind(tmp_path)
    for a in store.list_agents():
        store.update_agent(a["id"], {"enabled": False})
    with pytest.raises(ValueError, match="coordinator"):
        store.create_home_session()

"""Agent CRUD tests over the SQLite-backed store."""

from __future__ import annotations

import pytest

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "agents.db")


def test_list_agents_seeded(tmp_path) -> None:
    _rebind(tmp_path)
    agents = store.list_agents()
    assert {a["id"] for a in agents} == {"main", "ops", "research", "support"}
    main = next(a for a in agents if a["id"] == "main")
    assert main["is_super"] is True
    assert main["busy"] is False


def test_get_agent_found_and_missing(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.get_agent("main")["name"] == "Tomo"
    assert store.get_agent("nope") is None


def test_create_agent(tmp_path) -> None:
    _rebind(tmp_path)
    agent = store.create_agent(
        {"id": "dev", "name": "Dev", "description": "d", "model_id": "gpt-4o-mini"}
    )
    assert agent["id"] == "dev"
    assert agent["enabled"] is True
    assert store.get_agent("dev")["name"] == "Dev"


def test_create_agent_duplicate_raises(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_agent({"id": "main", "name": "dup"})


def test_update_agent(tmp_path) -> None:
    _rebind(tmp_path)
    updated = store.update_agent("main", {"name": "Tomo2", "enabled": False})
    assert updated is not None
    assert updated["name"] == "Tomo2"
    assert updated["enabled"] is False
    assert store.update_agent("nope", {"name": "x"}) is None


def test_delete_agent(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.delete_agent("support") is True
    assert store.get_agent("support") is None
    assert store.delete_agent("support") is False


def test_set_busy_reflected_in_list(tmp_path) -> None:
    _rebind(tmp_path)
    store.set_busy("ops", True)
    by_id = {a["id"]: a for a in store.list_agents()}
    assert by_id["ops"]["busy"] is True
    store.set_busy("ops", False)
    assert {a["id"]: a for a in store.list_agents()}["ops"]["busy"] is False

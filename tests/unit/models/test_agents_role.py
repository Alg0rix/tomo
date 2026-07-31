"""Agent ``role`` field — create / update / list, and empty ``model_id`` seed."""

from __future__ import annotations

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "agents-role.db")


def test_seed_agents_have_roles(tmp_path) -> None:
    _rebind(tmp_path)
    agents = {a["id"]: a for a in store.list_agents()}
    assert agents["main"]["role"]  # non-empty
    assert agents["ops"]["role"]
    assert agents["coder"]["role"]
    assert agents["research"]["role"]


def test_seed_agents_use_empty_model_id(tmp_path) -> None:
    """Seeded agents point at no profile (use default), not a model string."""
    _rebind(tmp_path)
    agents = {a["id"]: a for a in store.list_agents()}
    assert agents["main"]["model_id"] == ""
    assert agents["ops"]["model_id"] == ""
    assert agents["coder"]["model_id"] == ""


def test_create_agent_with_role(tmp_path) -> None:
    _rebind(tmp_path)
    agent = store.create_agent({"id": "dev", "name": "Dev", "role": "developer"})
    assert agent["role"] == "developer"
    assert store.get_agent("dev")["role"] == "developer"


def test_create_agent_role_defaults_empty(tmp_path) -> None:
    _rebind(tmp_path)
    agent = store.create_agent({"id": "dev", "name": "Dev"})
    assert agent["role"] == ""


def test_update_agent_role(tmp_path) -> None:
    _rebind(tmp_path)
    updated = store.update_agent("main", {"role": "lead coordinator"})
    assert updated is not None
    assert updated["role"] == "lead coordinator"
    assert store.get_agent("main")["role"] == "lead coordinator"

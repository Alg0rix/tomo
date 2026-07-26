"""Multi-workplace assignment on agents."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "multi_wp.db")


def test_agent_all_tunnels_scope(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace({"id": "t1", "name": "aio-serv", "kind": "tunnel"})
    store.create_workplace({"id": "t2", "name": "db-host", "kind": "tunnel"})
    store.create_workplace(
        {"id": "loc", "name": "local", "kind": "local", "root_path": str(tmp_path)}
    )
    updated = store.update_agent(
        "ops", {"workplace_scope": "all_tunnels", "workplace_id": "", "workplace_ids": []}
    )
    assert updated is not None
    assert updated["workplace_scope"] == "all_tunnels"
    assert store.get_agent("ops")["workplace_scope"] == "all_tunnels"


def test_agent_list_of_workplaces(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace({"id": "a", "name": "A", "kind": "tunnel"})
    store.create_workplace({"id": "b", "name": "B", "kind": "tunnel"})
    updated = store.update_agent(
        "ops",
        {
            "workplace_ids": ["a", "b"],
            "workplace_id": "a",
            "workplace_scope": "list",
        },
    )
    assert updated is not None
    assert updated["workplace_scope"] == "list"
    assert updated["workplace_ids"] == ["a", "b"]
    assert updated["workplace_id"] == "a"


def test_seed_ops_all_tunnels_on_fresh_db(tmp_path: Path) -> None:
    _rebind(tmp_path)
    ops = store.get_agent("ops")
    assert ops is not None
    assert ops["workplace_scope"] == "all_tunnels"

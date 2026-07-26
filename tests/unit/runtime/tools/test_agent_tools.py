"""Per-agent tool enablement persistence (SQLite agent_tools)."""

from __future__ import annotations

from app.runtime.tools.registry import get_registry, reset_registry
from app.services import store


def _rebind(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "tools.db")


def test_list_tools_comes_from_registry(tmp_path) -> None:
    _rebind(tmp_path)
    names = {t["id"] for t in store.list_tools()}
    assert names == set(get_registry().names())
    assert "bash" in names
    assert "calculator" in names
    assert "delegate" in names
    assert "read_file" in names
    assert "write_file" in names


def test_agent_tools_default_all_enabled(tmp_path) -> None:
    _rebind(tmp_path)
    rows = store.get_agent_tools("main")
    assert rows
    assert all(r["enabled"] for r in rows)


def test_set_agent_tools_persists(tmp_path) -> None:
    _rebind(tmp_path)
    enabled = {t["id"]: t["id"] in {"calculator", "bash"} for t in store.list_tools()}
    updated = store.set_agent_tools("main", enabled)
    assert updated is not None
    on = {t["id"] for t in updated if t["enabled"]}
    assert on == {"calculator", "bash"}
    # Reload view
    again = store.get_agent_tools("main")
    assert {t["id"] for t in again if t["enabled"]} == {"calculator", "bash"}
    agent = store.get_agent("main")
    assert agent is not None
    assert agent["tool_count"] == 2


def test_get_agent_openai_tools_filters(tmp_path) -> None:
    _rebind(tmp_path)
    store.set_agent_tools("main", {"calculator": True, "bash": False, "delegate": False,
                                   "read_file": False, "write_file": False})
    schemas = store.get_agent_openai_tools("main")
    names = {t["function"]["name"] for t in schemas}
    assert names == {"calculator"}

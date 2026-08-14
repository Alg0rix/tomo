"""MCP tools merged into the agent tool catalog + OpenAI schemas."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "mcp.db")


def _seed_server_with_tools(server_id: str = "srv", *, enabled: bool = True) -> None:
    store.create_mcp_server(
        {"id": server_id, "name": server_id, "transport": "stdio", "command": "echo", "enabled": enabled}
    )
    store.replace_mcp_items(
        server_id,
        [
            {
                "kind": "tool",
                "runtime_id": f"mcp__{server_id}__a",
                "name": "a",
                "description": "tool a",
                "schema": {
                    "type": "function",
                    "function": {"name": f"mcp__{server_id}__a", "description": "tool a", "parameters": {}},
                },
            },
            {
                "kind": "tool",
                "runtime_id": f"mcp__{server_id}__b",
                "name": "b",
                "description": "tool b",
                "schema": {
                    "type": "function",
                    "function": {"name": f"mcp__{server_id}__b", "description": "tool b", "parameters": {}},
                },
            },
        ],
    )


def test_get_agent_tools_includes_mcp_rows(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools()

    tools = store.get_agent_tools("main")
    row = next(t for t in tools if t["id"] == "mcp__srv__a")
    assert row["backend"] == "mcp:srv"
    assert row["enabled"] is True


def test_disabled_item_visible_in_catalog_but_absent_from_connected_schemas(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools()
    item = next(i for i in store.list_mcp_items("srv", kind="tool") if i["name"] == "a")
    store.set_mcp_item_enabled(item["id"], False)

    catalog = store.get_agent_tools("main")
    row = next(t for t in catalog if t["id"] == "mcp__srv__a")
    assert row["enabled"] is False  # still present, just off

    schemas = store.get_agent_openai_tools("main", connected_server_ids={"srv"})
    names = {s["function"]["name"] for s in schemas}
    assert "mcp__srv__a" not in names
    assert "mcp__srv__b" in names


def test_explicit_agent_tools_false_row_disables_only_that_mcp_tool(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools()
    store._conn.execute(
        "INSERT INTO agent_tools (agent_id, tool_id, enabled) VALUES (?,?,0)",
        ("main", "mcp__srv__a"),
    )
    store._conn.commit()

    tools = store.get_agent_tools("main")
    a = next(t for t in tools if t["id"] == "mcp__srv__a")
    b = next(t for t in tools if t["id"] == "mcp__srv__b")
    assert a["enabled"] is False
    assert b["enabled"] is True


def test_disabled_server_removes_all_its_mcp_tools_from_agent_schemas(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools()

    store.update_mcp_server("srv", {"enabled": False})

    schemas = store.get_agent_openai_tools("main", connected_server_ids={"srv"})
    names = {s["function"]["name"] for s in schemas}
    assert "mcp__srv__a" not in names
    assert "mcp__srv__b" not in names

    catalog = store.get_agent_tools("main")
    row = next(t for t in catalog if t["id"] == "mcp__srv__a")
    assert row["enabled"] is False
    assert row["locked"] is True


def test_mcp_tools_excluded_from_schemas_when_server_not_connected(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools()

    schemas = store.get_agent_openai_tools("main", connected_server_ids=set())
    names = {s["function"]["name"] for s in schemas}
    assert "mcp__srv__a" not in names
    assert "mcp__srv__b" not in names

    # But the UI catalog (no connected filter) still shows them, enabled.
    catalog = store.get_agent_tools("main")
    row = next(t for t in catalog if t["id"] == "mcp__srv__a")
    assert row["enabled"] is True


def test_list_mcp_server_ids_for_agent(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _seed_server_with_tools("srv1")
    _seed_server_with_tools("srv2")
    store._conn.execute(
        "INSERT INTO agent_tools (agent_id, tool_id, enabled) VALUES (?,?,0)",
        ("main", "mcp__srv2__a"),
    )
    store._conn.execute(
        "INSERT INTO agent_tools (agent_id, tool_id, enabled) VALUES (?,?,0)",
        ("main", "mcp__srv2__b"),
    )
    store._conn.commit()

    ids = store.list_mcp_server_ids_for_agent("main")
    assert ids == {"srv1"}  # srv2's tools are all disabled for this agent


def test_execute_async_routes_mcp_calls_to_manager(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from app.runtime.tools import registry
    from app.runtime.mcp import mcp_manager

    calls = []

    async def fake_call_tool(runtime_id, arguments):
        calls.append((runtime_id, arguments))
        return "ok"

    monkeypatch.setattr(mcp_manager, "call_tool", fake_call_tool)

    out = asyncio.run(registry.execute_async("mcp__srv__a", {"x": 1}))
    assert out == "ok"
    assert calls == [("mcp__srv__a", {"x": 1})]


def test_execute_async_runs_builtins_in_thread(tmp_path: Path) -> None:
    import asyncio

    from app.runtime.tools import registry

    out = asyncio.run(registry.execute_async("bash", {"command": "echo hi"}))
    assert "hi" in out


def test_is_mcp_tool_name() -> None:
    from app.runtime.tools.registry import is_mcp_tool_name

    assert is_mcp_tool_name("mcp__srv__a")
    assert not is_mcp_tool_name("bash")

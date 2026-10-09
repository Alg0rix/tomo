"""Discovery shrinks schemas without widening execution permissions."""
import json

import pytest

from app.runtime.tools.discovery import CORE_TOOLS, ToolDiscovery, bind, reset, run
from app.runtime.tools.registry import execute
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture
def owned_catalog(tmp_path):
    store.rebind(tmp_path / "discovery.db")
    with owned_admin_scope() as (context, _):
        yield context, store.get_agent_openai_tools(context.agent_id)


def names(schemas):
    return {s["function"]["name"] for s in schemas}


def test_discovery_dispatch_loads_only_matches(owned_catalog):
    _, catalog = owned_catalog
    discovery = ToolDiscovery(catalog)
    assert names(discovery.schemas()) == names(catalog) & CORE_TOOLS
    assert "list_artifacts" not in names(discovery.schemas())
    token = bind(discovery)
    try:
        result = json.loads(execute("search_tools", {"query": "list_artifacts", "limit": 1}))
        assert result["loaded_tools"] == ["list_artifacts"]
        assert "list_artifacts" in names(discovery.schemas())
        assert "delete_file" not in names(discovery.schemas())
        assert len(json.dumps(discovery.schemas())) < len(json.dumps(catalog))
    finally:
        reset(token)
    assert run({"query": "list_artifacts"}).startswith("Error:")


def test_permission_changes_remove_search_results_and_loaded_schemas(owned_catalog):
    context, catalog = owned_catalog
    discovery = ToolDiscovery(catalog)
    discovery.search({"query": "delete_file", "limit": 1})
    assert "delete_file" in names(discovery.schemas())
    enabled = store.get_enabled_tool_ids(context.agent_id) - {"delete_file"}
    store.set_agent_tools(context.agent_id, {name: True for name in enabled})
    assert "delete_file" not in names(discovery.schemas())
    assert "delete_file" not in discovery.loaded
    assert "delete_file" not in {
        r["name"] for r in json.loads(discovery.search({"query": "delete_file"}))["results"]
    }


def test_discovery_never_widens_supplied_catalog(owned_catalog):
    _, catalog = owned_catalog
    discovery = ToolDiscovery([s for s in catalog if s["function"]["name"] in CORE_TOOLS])
    assert "delete_file" not in {
        r["name"] for r in json.loads(discovery.search({"query": "delete_file"}))["results"]
    }


def test_discovery_budget_and_pagination(owned_catalog):
    _, catalog = owned_catalog
    discovery = ToolDiscovery(catalog, context_window=100, target_percent=1)
    result = json.loads(discovery.search({"query": "list_artifacts", "limit": 1}))
    assert result["over_target"] is True
    assert result["schema_target_tokens"] == 1
    assert result["loaded_tools"] == ["list_artifacts"]
    assert result["results"][0]["loaded"] is True
    first = json.loads(discovery.search({"query": "*", "limit": 5}))
    second = json.loads(discovery.search({"query": "*", "limit": 5, "offset": first["next_offset"]}))
    assert {r["name"] for r in first["results"]}.isdisjoint(r["name"] for r in second["results"])


@pytest.mark.parametrize("arguments", [
    {"query": ""}, {"query": "x" * 513}, {"query": None},
    {"query": "*", "limit": True}, {"query": "*", "limit": 10},
    {"query": "*", "offset": -1},
])
def test_invalid_search_arguments(owned_catalog, arguments):
    assert ToolDiscovery(owned_catalog[1]).search(arguments).startswith("Error:")


def test_restore_pairs_only_tool_results_and_intersects_catalog(owned_catalog):
    _, catalog = owned_catalog
    discovery = ToolDiscovery(catalog)
    result = json.dumps({"loaded_tools": ["list_artifacts", "plugin__foreign__secret"]})
    discovery.restore([{"role": "user", "content": result}])
    assert "list_artifacts" not in names(discovery.schemas())
    discovery.restore([
        {"role": "assistant", "tool_calls": [{"id": "search", "function": {"name": "search_tools"}}]},
        {"role": "tool", "tool_call_id": "search", "content": result},
    ])
    assert "list_artifacts" in names(discovery.schemas())
    assert "plugin__foreign__secret" not in names(discovery.schemas())


def test_nested_scope_does_not_inherit_another_catalog(owned_catalog):
    outer = ToolDiscovery(owned_catalog[1])
    token = bind(outer)
    nested = bind(None)
    try:
        assert run({"query": "*"}).startswith("Error:")
    finally:
        reset(nested)
    try:
        assert json.loads(run({"query": "list_artifacts", "limit": 1}))["loaded_tools"] == ["list_artifacts"]
    finally:
        reset(token)


def test_restore_retains_required_tools_above_soft_target(owned_catalog):
    _, catalog = owned_catalog
    discovery = ToolDiscovery(catalog, context_window=100)
    discovery.restore([
        {"role": "assistant", "tool_calls": [{"id": "old", "function": {"name": "delete_file"}}]},
        {"role": "assistant", "tool_calls": [{"id": "new", "function": {"name": "list_artifacts"}}]},
    ])
    assert "list_artifacts" in names(discovery.schemas())
    assert "delete_file" in names(discovery.schemas())
    assert discovery.budget_status()["over_target"] is True


@pytest.mark.parametrize("window,target", [(32_000, 1600), (128_000, 6400), (200_000, 10_000)])
def test_target_scales_with_model_context(owned_catalog, window, target):
    discovery = ToolDiscovery(owned_catalog[1], context_window=window)
    assert discovery.budget_status()["schema_target_tokens"] == target


def test_core_counts_toward_target_and_unknown_window_is_not_guessed(owned_catalog):
    discovery = ToolDiscovery(owned_catalog[1], context_window=100)
    assert discovery.budget_status()["over_target"] is True  # Core alone exceeds 5 tokens.
    discovery.context_window = None
    assert discovery.budget_status()["schema_target_tokens"] is None
    assert discovery.budget_status()["over_target"] is False


@pytest.mark.parametrize("value", [0, -1, 101, True, "5", None, float("nan"), float("inf")])
def test_invalid_percentage_setting_is_rejected(owned_catalog, value):
    with pytest.raises(ValueError, match="Tool schema target"):
        store.update_settings({"tool_schema_target_percent": value})
    assert store.get_settings()["tool_schema_target_percent"] == 5.0


def test_percentage_setting_is_configurable(owned_catalog):
    store.update_settings({"tool_schema_target_percent": 10.5})
    discovery = ToolDiscovery(owned_catalog[1], context_window=200_000,
                              target_percent=store.get_settings()["tool_schema_target_percent"])
    assert discovery.budget_status()["schema_target_tokens"] == 21_000

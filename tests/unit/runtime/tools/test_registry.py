"""Tool registry: JSON schema discovery and dispatch tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime.tools.registry import ToolRegistry, execute, get_openai_tools


# --- schema discovery (real app/tools/ dir) ------------------------------


def test_registry_loads_calculator_schema() -> None:
    tools = get_openai_tools()
    assert isinstance(tools, list)
    assert len(tools) >= 1
    calc = next(t for t in tools if t.get("function", {}).get("name") == "calculator")
    assert calc["type"] == "function"
    params = calc["function"]["parameters"]
    assert params["type"] == "object"
    assert "expression" in params["properties"]
    assert params["required"] == ["expression"]


def test_get_openai_tools_shape_is_openai_compatible() -> None:
    """Schemas must be passable straight into LLMClient.complete(tools=...)."""
    for tool in get_openai_tools():
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]


def test_registry_names_include_calculator() -> None:
    assert "calculator" in ToolRegistry().names()


# --- dispatch (real calculator backend) ---------------------------------


def test_execute_calculator_returns_string_result() -> None:
    assert execute("calculator", {"expression": "2 + 2"}) == "4"


def test_execute_calculator_multiplication() -> None:
    assert execute("calculator", {"expression": "6 * 7"}) == "42"


def test_execute_calculator_error_on_div_zero() -> None:
    assert execute("calculator", {"expression": "1 / 0"}).startswith("Error")


def test_execute_unknown_tool_returns_error_string() -> None:
    result = execute("does_not_exist", {})
    assert result.startswith("Error")
    assert "does_not_exist" in result


def test_execute_non_dict_arguments_returns_error_string() -> None:
    assert execute("calculator", "not a dict").startswith("Error")  # type: ignore[arg-type]


def test_execute_never_raises_on_bad_expression() -> None:
    # Registry must surface calculator errors as strings, never propagate.
    assert execute("calculator", {"expression": "2 +"}).startswith("Error")


# --- isolated loading from a temp tools dir -----------------------------


def _write_tool(path: Path, **fields: object) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


def test_loads_tools_from_custom_dir(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    _write_tool(
        tools_dir / "calculator.json",
        id="calculator",
        schema={
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "calc",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    )

    reg = ToolRegistry(tools_dir=tools_dir)
    assert reg.names() == ["calculator"]
    assert reg.get_openai_tools()[0]["function"]["name"] == "calculator"


def test_skips_malformed_json(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")
    reg = ToolRegistry(tools_dir=tools_dir)
    assert reg.names() == []


def test_definition_without_function_schema_not_exposed_as_tool(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    _write_tool(tools_dir / "nope.json", id="nope", schema={"type": "not_function"})
    reg = ToolRegistry(tools_dir=tools_dir)
    # Loaded as a definition (has an id) but not exposed as an OpenAI tool.
    assert reg.names() == ["nope"]
    assert reg.get_openai_tools() == []


def test_missing_tools_dir_is_empty_registry(tmp_path: Path) -> None:
    reg = ToolRegistry(tools_dir=tmp_path / "does_not_exist")
    assert reg.names() == []
    assert reg.get_openai_tools() == []


def test_execute_tool_without_backend_is_error(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    _write_tool(
        tools_dir / "ghost.json",
        id="ghost",
        schema={
            "type": "function",
            "function": {
                "name": "ghost",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    )
    reg = ToolRegistry(tools_dir=tools_dir)
    result = reg.execute("ghost", {})
    assert result.startswith("Error")
    assert "backend" in result

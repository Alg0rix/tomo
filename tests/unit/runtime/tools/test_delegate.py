"""Delegate tool: membership-safe handoff strings."""

from __future__ import annotations

import pytest

from app.runtime.tools import delegate as delegate_tool
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_registry()
    yield
    reset_registry()


@pytest.fixture(autouse=True)
def _clear_delegate_ctx() -> None:
    delegate_tool.reset_context()
    yield
    delegate_tool.reset_context()


def test_delegate_schema_loaded() -> None:
    tools = get_openai_tools()
    schema = next(t for t in tools if t["function"]["name"] == "delegate")
    props = schema["function"]["parameters"]["properties"]
    assert "agent_id" in props or "name" in props or "agent" in props


def test_delegate_to_session_member_by_id() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
        ],
    )
    result = execute("delegate", {"agent_id": "ops", "reason": "disk check"})
    assert result == "Delegated to ops"


def test_delegate_to_session_member_by_name() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
        ],
    )
    result = execute("delegate", {"name": "Ops"})
    assert result == "Delegated to ops"


def test_delegate_rejects_non_member() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
            {"id": "research", "name": "Research"},
        ],
    )
    result = execute("delegate", {"agent_id": "research"})
    assert result.startswith("Error:")
    assert "research" in result.lower() or "not" in result.lower()


def test_delegate_requires_target() -> None:
    delegate_tool.bind_context(agent_ids=["main"], agents=[{"id": "main", "name": "Tomo"}])
    result = execute("delegate", {})
    assert result.startswith("Error:")


def test_delegate_without_context_is_error() -> None:
    result = execute("delegate", {"agent_id": "ops"})
    assert result.startswith("Error:")

"""Coordinator router: membership-safe target resolution and @mention parse."""

from __future__ import annotations

from app.runtime.coordinator.router import resolve_target

_AGENTS = [
    {"id": "main", "name": "Tomo"},
    {"id": "ops", "name": "Ops"},
    {"id": "research", "name": "Research"},
]
_MEMBERS = ["main", "ops", "research"]






def test_resolve_target_with_at_prefix() -> None:
    assert (
        resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="@Ops") == "ops"
    )


def test_resolve_target_rejects_non_member() -> None:
    # support exists in agents list but is not a session member
    agents = _AGENTS + [{"id": "support", "name": "Support"}]
    assert (
        resolve_target(agent_ids=_MEMBERS, agents=agents, query="support") is None
    )
    assert (
        resolve_target(agent_ids=["main"], agents=_AGENTS, query="ops") is None
    )


def test_resolve_target_empty_query() -> None:
    assert resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="") is None
    assert resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="   ") is None


def test_resolve_target_unique_prefix() -> None:
    assert resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="re") == "research"
    assert resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="op") == "ops"


def test_resolve_target_by_role() -> None:
    agents = [
        {"id": "main", "name": "Tomo", "role": "coordinator"},
        {"id": "ops", "name": "Ops", "role": "ops"},
    ]
    assert (
        resolve_target(agent_ids=["main", "ops"], agents=agents, query="coordinator")
        == "main"
    )





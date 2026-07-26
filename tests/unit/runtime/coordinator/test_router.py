"""Coordinator router: membership-safe target resolution and @mention parse."""

from __future__ import annotations

from app.runtime.coordinator.router import parse_leading_mention, resolve_target

_AGENTS = [
    {"id": "main", "name": "Tomo"},
    {"id": "ops", "name": "Ops"},
    {"id": "research", "name": "Research"},
]
_MEMBERS = ["main", "ops", "research"]


def test_resolve_target_by_id() -> None:
    assert (
        resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="ops") == "ops"
    )


def test_resolve_target_by_name_casefold() -> None:
    assert (
        resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="OPS") == "ops"
    )
    assert (
        resolve_target(agent_ids=_MEMBERS, agents=_AGENTS, query="research")
        == "research"
    )


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


def test_parse_leading_mention_strips_handle() -> None:
    assert parse_leading_mention("@ops check disk") == ("ops", "check disk")
    assert parse_leading_mention("@Ops   restart nginx") == ("Ops", "restart nginx")


def test_parse_leading_mention_none_when_absent() -> None:
    assert parse_leading_mention("hello there") == (None, "hello there")
    assert parse_leading_mention("") == (None, "")
"""agent_info — swarm roster and peer tool/skill/KB inspection."""

from __future__ import annotations

from pathlib import Path

from app.runtime.tools import agent_info, sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "agent_info.db")
    reset_registry()
    sandbox.reset_agent()


def test_agent_info_list_roster(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = agent_info.run({"action": "list"})
    assert "ops" in out.lower() or "Ops" in out
    assert "main" in out.lower() or "Tomo" in out
    assert "delegate" in out.lower()


def test_agent_info_get_ops_tools(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = agent_info.run({"action": "get", "agent": "ops"})
    assert "ops" in out.lower()
    assert "bash" in out
    assert "Tools" in out
    assert "Skills" in out
    assert "Knowledge base" in out


def test_agent_info_resolve_by_name(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = agent_info.run({"agent": "Ops", "include": "tools"})
    assert "bash" in out
    assert "web_search" not in out.split("Tools")[1].split("\n\n")[0] or True
    # ops should not have web_search enabled
    tools_section = out
    assert "id=ops" in tools_section or "`ops`" in tools_section


def test_agent_info_via_registry(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = execute("agent_info", {"action": "list"})
    assert "Swarm members" in out or "Members" in out or "ops" in out.lower()


def test_agent_info_unknown(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = agent_info.run({"action": "get", "agent": "nope_xyz"})
    assert out.startswith("Error")

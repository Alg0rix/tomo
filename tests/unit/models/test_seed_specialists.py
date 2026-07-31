"""Seeded specialist prompts and tool allow-lists."""

from __future__ import annotations

from app.core import config, home
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "seed-specialists.db")


def test_seed_writes_specialist_system_prompts(tmp_path) -> None:
    _rebind(tmp_path)
    for aid, title in (("ops", "Ops"), ("coder", "Coder"), ("research", "Research")):
        path = home.agent_system_path(aid, config.TOMO_HOME)
        assert path.is_file(), f"missing SYSTEM.md for {aid}"
        text = path.read_text(encoding="utf-8")
        assert f"# {title}" in text
        assert "Mission" in text


def test_seed_specialist_tool_allowlists(tmp_path) -> None:
    _rebind(tmp_path)
    agents = {a["id"]: a for a in store.list_agents()}
    assert agents["ops"]["tool_count"] == 11
    assert agents["coder"]["tool_count"] == 15
    assert agents["research"]["tool_count"] == 10

    ops_tools = {t["id"]: t["enabled"] for t in store.get_agent_tools("ops")}
    assert ops_tools.get("bash") is True
    assert ops_tools.get("web_search") is False

    coder_tools = {t["id"]: t["enabled"] for t in store.get_agent_tools("coder")}
    assert coder_tools.get("str_replace") is True
    assert coder_tools.get("web_fetch") is False

    research_tools = {t["id"]: t["enabled"] for t in store.get_agent_tools("research")}
    assert research_tools.get("web_search") is True
    assert research_tools.get("bash") is False

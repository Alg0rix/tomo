"""Skill awareness catalog injected into the system prompt."""

from __future__ import annotations

from app.core import config, home
from app.runtime.agent.context import build_system_prompt
from app.runtime.agent.skills_prompt import build_skills_system_prompt
from app.services import store


def _install_skill(skill_id: str, description: str) -> None:
    d = home.library_skills_dir(config.TOMO_HOME) / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )
    store.sync_skills()


def test_skills_prompt_lists_enabled_skills(tmp_path) -> None:
    store.rebind(tmp_path / "skills_prompt.db")
    _install_skill("hallmark", "Anti-AI-slop design system for distinctive UI")
    block = build_skills_system_prompt("main")
    assert "## Skills" in block
    assert "use_skill(skill_id=" in block
    assert "<available_skills>" in block
    assert "hallmark:" in block
    assert "Anti-AI-slop" in block
    assert "manage_skill" in block


def test_skills_prompt_guidance_without_catalog(tmp_path) -> None:
    store.rebind(tmp_path / "skills_empty_catalog.db")
    with store._lock:
        store._conn.execute("DELETE FROM agent_skills")
        store._conn.execute("DELETE FROM skills")
        store._conn.commit()
    block = build_skills_system_prompt("main")
    assert "## Skills" in block
    assert "No skills are installed" in block
    assert "manage_skill" in block


def test_skills_prompt_marks_assigned(tmp_path) -> None:
    store.rebind(tmp_path / "skills_assigned.db")
    _install_skill("hallmark", "Design system")
    _install_skill("other-skill", "Other procedure")
    store.set_agent_skills("main", ["hallmark"])
    block = build_skills_system_prompt("main")
    assert "hallmark*:" in block
    assert "other-skill:" in block
    assert "assigned to you" in block
    # Assigned first
    assert block.index("hallmark*") < block.index("other-skill:")


def test_skills_prompt_empty_without_agent() -> None:
    assert build_skills_system_prompt(None) == ""


def test_skills_prompt_skipped_when_skill_tools_disabled(tmp_path) -> None:
    store.rebind(tmp_path / "skills_disabled_tools.db")
    _install_skill("hallmark", "Design system")
    # Explicit allowlist without skill tools.
    store.set_agent_tools("main", {"bash": True, "read_file": True})
    assert build_skills_system_prompt("main") == ""


def test_build_system_prompt_includes_skills_section(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    store.rebind(tmp_path / "skills_in_system.db")
    home.ensure_tomo_home(tmp_path)
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    d = home.library_skills_dir(tmp_path) / "demo-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo procedure\n---\n\nBody.\n",
        encoding="utf-8",
    )
    store.sync_skills()
    text = build_system_prompt("main", home_root=tmp_path)
    assert "## Skills" in text
    assert "demo-skill:" in text
    assert "use_skill(skill_id=" in text
    assert "manage_skill" in text


def test_memory_guidance_gated_on_memory_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    store.rebind(tmp_path / "mem_gate.db")
    home.ensure_tomo_home(tmp_path)
    store.set_agent_tools("ops", {"bash": True, "read_file": True})
    text = build_system_prompt("ops", home_root=tmp_path)
    assert "persistent curated memory" not in text

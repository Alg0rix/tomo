"""Bundled swarm instructions are a real discoverable SKILL.md package."""

from app.extensions.skills import find_discovered_skill


def test_internal_swarm_skill_is_discoverable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    skill = find_discovered_skill("swarm", home_root=tmp_path)
    assert skill is not None
    assert skill.source == "internal"
    assert skill.skill_md.name == "SKILL.md"
    assert skill.body

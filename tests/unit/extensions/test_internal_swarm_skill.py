"""The bundled reference is one discoverable skill named tomo."""

from app.extensions.skills import find_discovered_skill, read_skill_file
from app.runtime.agent.skills_prompt import _SKILL_DESC_LIMIT


def test_internal_tomo_skill_is_discoverable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    skill = find_discovered_skill("tomo", home_root=tmp_path)
    assert skill is not None
    assert skill.source == "internal"
    assert skill.id == "tomo"
    assert len(skill.description) <= _SKILL_DESC_LIMIT
    assert find_discovered_skill("swarm", home_root=tmp_path) is None
    assert find_discovered_skill("connector", home_root=tmp_path) is None
    body = read_skill_file("tomo", "references/swarm.md", home_root=tmp_path)
    assert "start_swarm" in body

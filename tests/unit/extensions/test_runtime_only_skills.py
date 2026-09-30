"""Swarm guidance is available to the root agent for runtime handoffs."""

from app.extensions.skills import is_runtime_only_skill, read_skill_file
from app.runtime.tools.skills_tools import use_skill_run


def test_swarm_internal_skill_is_runtime_only() -> None:
    assert not is_runtime_only_skill("swarm", "internal")
    assert not is_runtime_only_skill("swarm", "library")
    assert not is_runtime_only_skill("git-commit", "internal")


def test_use_skill_can_read_swarm_guidance() -> None:
    out = use_skill_run({"skill_id": "tomo", "file": "references/swarm.md"})
    assert out.startswith("Skill file:")
    body = read_skill_file("tomo", "references/swarm.md")
    assert "start_swarm" in body
    assert body in out

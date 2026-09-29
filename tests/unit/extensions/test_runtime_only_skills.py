"""Runtime skills are hidden from catalogs but remain readable as guidance."""

from app.extensions.skills import is_runtime_only_skill
from app.runtime.tools.skills_tools import use_skill_run


def test_swarm_internal_skill_is_runtime_only() -> None:
    assert is_runtime_only_skill("swarm", "internal")
    assert not is_runtime_only_skill("swarm", "library")
    assert not is_runtime_only_skill("git-commit", "internal")


def test_use_skill_can_read_swarm_guidance() -> None:
    out = use_skill_run({"skill_id": "swarm"})
    assert out.startswith("Skill:")
    assert "## Consent" in out
    assert "does not itself launch workers" in out

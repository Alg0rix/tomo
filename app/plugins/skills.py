"""Read namespaced skills without executing plugin code."""

from dataclasses import replace
from pathlib import Path


def load_plugin_skills(path: Path, plugin_id: str) -> list:
    from app.extensions.skills import iter_skill_md_files, load_discovered_skill

    skills = []
    seen = set()
    for skill_md in iter_skill_md_files(path / "skills"):
        skill = load_discovered_skill(skill_md, "plugin:" + plugin_id, root=path)
        if skill is None:
            raise ValueError(
                "Plugin skill must be readable and stay inside its package"
            )
        skill_id = f"plugin__{plugin_id}__{skill.id}"
        if skill_id in seen:
            raise ValueError("Duplicate plugin skill: " + skill.id)
        seen.add(skill_id)
        skills.append(replace(skill, id=skill_id))
    return skills

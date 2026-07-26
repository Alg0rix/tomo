"""list_skills / use_skill tool backends."""

from __future__ import annotations

from typing import Any


def list_skills_run(arguments: dict[str, Any]) -> str:
    """List skills from the store; always returns a string."""
    if arguments is not None and not isinstance(arguments, dict):
        return "Error: list_skills expects a dict of arguments"

    from app.services import store

    skills = store.list_skills()
    if not skills:
        return "No skills registered"
    lines = []
    for s in skills:
        flag = "on" if s.get("enabled") else "off"
        lines.append(
            f"{s.get('id')}: {s.get('name')} [{flag}] — {s.get('description', '')}"
        )
    return "\n".join(lines)


def use_skill_run(arguments: dict[str, Any]) -> str:
    """Return a skill's description/body; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: use_skill expects a dict of arguments"
    skill_id = arguments.get("skill_id") or arguments.get("id")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return "Error: 'skill_id' argument must be a non-empty string"
    skill_id = skill_id.strip()

    from app.services import store

    skill = store.get_skill(skill_id)
    if skill is None:
        return f"Error: unknown skill {skill_id!r}"
    name = skill.get("name") or skill_id
    desc = (skill.get("description") or "").strip()
    version = skill.get("version") or ""
    enabled = "enabled" if skill.get("enabled") else "disabled"
    parts = [f"Skill: {name} ({skill_id})", f"Status: {enabled}"]
    if version:
        parts.append(f"Version: {version}")
    parts.append("")
    parts.append(desc or "(no description)")
    return "\n".join(parts)


# Registry expects ``run`` on each module; thin wrappers below keep one file.


def run(arguments: dict[str, Any]) -> str:
    """Default entry used only if registered as list_skills via this module."""
    return list_skills_run(arguments)


__all__ = ["list_skills_run", "use_skill_run", "run"]

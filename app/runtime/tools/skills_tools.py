"""list_skills / use_skill tool backends."""

from __future__ import annotations

from typing import Any


def list_skills_run(arguments: dict[str, Any]) -> str:
    """List skills from the store; always returns a string."""
    if arguments is not None and not isinstance(arguments, dict):
        return "Error: list_skills expects a dict of arguments"

    from app.services import store

    try:
        store.sync_skills()
    except Exception:
        pass
    skills = store.list_skills()
    if not skills:
        return "No skills registered"
    lines = []
    for s in skills:
        if not s.get("enabled", True):
            continue
        src = s.get("source") or "catalog"
        lines.append(
            f"{s.get('id')}: {s.get('name')} [{src}] — {s.get('description', '')}"
        )
    return "\n".join(lines) if lines else "No skills registered"


def use_skill_run(arguments: dict[str, Any]) -> str:
    """Return a skill's body from disk (or DB description); always a string."""
    if not isinstance(arguments, dict):
        return "Error: use_skill expects a dict of arguments"
    skill_id = arguments.get("skill_id") or arguments.get("id") or arguments.get("name")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return "Error: 'skill_id' argument must be a non-empty string"
    skill_id = skill_id.strip()

    from app.extensions.skills import read_skill_body, slugify_skill_id
    from app.services import store

    try:
        store.sync_skills()
    except Exception:
        pass

    sid = slugify_skill_id(skill_id)
    skill = store.get_skill(sid) or store.get_skill(skill_id)
    body = read_skill_body(sid)
    if body is None and skill is None:
        return f"Error: unknown skill {skill_id!r}"
    name = (skill or {}).get("name") or skill_id
    version = (skill or {}).get("version") or ""
    source = (skill or {}).get("source") or ""
    parts = [f"Skill: {name} ({sid})"]
    if source:
        parts.append(f"Source: {source}")
    if version:
        parts.append(f"Version: {version}")
    parts.append("")
    if body:
        parts.append(body)
    else:
        parts.append(((skill or {}).get("description") or "").strip() or "(no body)")
    return "\n".join(parts)


def run(arguments: dict[str, Any]) -> str:
    """Default entry used only if registered as list_skills via this module."""
    return list_skills_run(arguments)


__all__ = ["list_skills_run", "use_skill_run", "run"]

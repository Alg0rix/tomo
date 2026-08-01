"""list_skills / use_skill / manage_skill tool backends."""

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
    """Return a skill's body (or a support file) from disk; always a string."""
    if not isinstance(arguments, dict):
        return "Error: use_skill expects a dict of arguments"
    skill_id = arguments.get("skill_id") or arguments.get("id") or arguments.get("name")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return "Error: 'skill_id' argument must be a non-empty string"
    skill_id = skill_id.strip()
    file_path = (
        arguments.get("file")
        or arguments.get("path")
        or arguments.get("reference")
        or arguments.get("file_path")
        or ""
    )
    if file_path is not None and not isinstance(file_path, str):
        file_path = str(file_path)
    file_path = (file_path or "").strip()

    from app.extensions.skills import (
        find_discovered_skill,
        list_skill_support_files,
        read_skill_body,
        read_skill_file,
        slugify_skill_id,
    )
    from app.services import store

    try:
        store.sync_skills()
    except Exception:
        pass

    sid = slugify_skill_id(skill_id)
    skill = store.get_skill(sid) or store.get_skill(skill_id)
    discovered = find_discovered_skill(sid)

    if file_path:
        try:
            content = read_skill_file(sid, file_path)
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: failed to read skill file: {exc}"
        name = (skill or {}).get("name") or (discovered.name if discovered else skill_id)
        return (
            f"Skill file: {name} ({sid}) → {file_path}\n\n"
            f"{content}"
        )

    body = read_skill_body(sid)
    if body is None and skill is None and discovered is None:
        return f"Error: unknown skill {skill_id!r}"
    try:
        store.bump_skill_use(sid)
    except Exception:
        pass
    name = (skill or {}).get("name") or (discovered.name if discovered else skill_id)
    version = (skill or {}).get("version") or (discovered.version if discovered else "")
    source = (skill or {}).get("source") or (discovered.source if discovered else "")
    parts = [f"Skill: {name} ({sid})"]
    if source:
        parts.append(f"Source: {source}")
    if version:
        parts.append(f"Version: {version}")
    if discovered is not None:
        parts.append(f"Root: {discovered.path}")
    support = list_skill_support_files(sid, limit=40)
    if support:
        parts.append(
            "Support files (load with use_skill skill_id="
            f"{sid} file=<path> — do NOT use read_file with absolute paths):"
        )
        for rel in support[:25]:
            parts.append(f"  - {rel}")
        if len(support) > 25:
            parts.append(f"  … +{len(support) - 25} more")
    parts.append("")
    if body:
        parts.append(body)
    else:
        parts.append(((skill or {}).get("description") or "").strip() or "(no body)")
    return "\n".join(parts)


def _assign_skill_to_agent(skill_id: str, agent_id: str | None) -> None:
    if not agent_id:
        return
    from app.services import store

    try:
        rows = store.get_agent_skills(agent_id)
        current = [s["id"] for s in rows if s.get("assigned")]
        if skill_id not in current:
            store.set_agent_skills(agent_id, current + [skill_id])
    except Exception:
        pass


def manage_skill_run(arguments: dict[str, Any]) -> str:
    """Create / edit / patch / delete library skills (active learning write path)."""
    if not isinstance(arguments, dict):
        return "Error: manage_skill expects a dict of arguments"

    action = str(arguments.get("action") or "").strip().lower()
    skill_id = arguments.get("skill_id") or arguments.get("name") or ""
    if not isinstance(skill_id, str):
        skill_id = str(skill_id or "")
    skill_id = skill_id.strip()
    agent_id = arguments.get("agent_id")
    if agent_id is not None:
        agent_id = str(agent_id).strip() or None
    if not agent_id:
        try:
            from app.runtime.tools.sandbox import current_agent_id

            agent_id = current_agent_id()
        except Exception:
            agent_id = None

    from app.extensions import skills as skills_ext
    from app.services import store

    try:
        if action == "create":
            if not skill_id:
                return "Error: skill_id (or name) is required"
            name = str(arguments.get("display_name") or skill_id).strip()
            description = str(arguments.get("description") or "").strip()
            body = str(arguments.get("body") or "").strip()
            version = str(arguments.get("version") or "1.0").strip() or "1.0"
            extra = {
                "origin": "learned",
                "agent": agent_id or "",
            }
            skill = skills_ext.write_library_skill(
                skill_id=skill_id,
                name=name,
                description=description,
                body=body,
                version=version,
                extra_meta=extra,
                overwrite=bool(arguments.get("overwrite")),
            )
            store.sync_skills()
            _assign_skill_to_agent(skill.id, agent_id)
            return (
                f"Created skill '{skill.id}' ({skill.name}). "
                f"Description: {skill.description}"
            )

        if action == "edit":
            if not skill_id:
                return "Error: skill_id is required"
            content = arguments.get("content")
            skill = skills_ext.edit_library_skill(
                skill_id,
                content=str(content) if content is not None else None,
                name=str(arguments["display_name"]).strip()
                if arguments.get("display_name")
                else None,
                description=str(arguments["description"]).strip()
                if arguments.get("description")
                else None,
                body=str(arguments["body"]) if arguments.get("body") is not None else None,
                version=str(arguments["version"]).strip()
                if arguments.get("version")
                else None,
            )
            store.sync_skills()
            return f"Updated skill '{skill.id}' ({skill.name})."

        if action == "patch":
            if not skill_id:
                return "Error: skill_id is required"
            old = arguments.get("old_string")
            new = arguments.get("new_string")
            if not isinstance(old, str) or not old:
                return "Error: old_string is required"
            if not isinstance(new, str):
                return "Error: new_string is required"
            skill = skills_ext.patch_library_skill(
                skill_id,
                old_string=old,
                new_string=new,
                file_path=str(arguments.get("file_path") or "SKILL.md"),
            )
            store.sync_skills()
            return f"Patched skill '{skill.id}'."

        if action == "write_file":
            if not skill_id:
                return "Error: skill_id is required"
            file_path = str(arguments.get("file_path") or "").strip()
            content = arguments.get("content")
            if not isinstance(content, str):
                return "Error: content is required"
            path = skills_ext.write_skill_support_file(
                skill_id, file_path=file_path, content=content
            )
            return f"Wrote support file {path.name} under skill '{skills_ext.slugify_skill_id(skill_id)}'."

        if action == "delete":
            if not skill_id:
                return "Error: skill_id is required"
            sid = skills_ext.slugify_skill_id(skill_id)
            ok = skills_ext.delete_library_skill(sid)
            if not ok:
                return f"Error: library skill not found: {sid}"
            try:
                store.delete_skill(sid)
            except Exception:
                store.sync_skills()
            return f"Deleted skill '{sid}'."

        return (
            "Error: action must be one of create, edit, patch, write_file, delete"
        )
    except FileExistsError as exc:
        return f"Error: {exc}"
    except FileNotFoundError as exc:
        return f"Error: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: manage_skill failed: {exc}"


def run(arguments: dict[str, Any]) -> str:
    """Default entry used only if registered as list_skills via this module."""
    return list_skills_run(arguments)


__all__ = [
    "list_skills_run",
    "use_skill_run",
    "manage_skill_run",
    "run",
]

"""list_skills / use_skill / manage_skill tool backends."""

from __future__ import annotations

from typing import Any

_DEFAULT_LIST_LIMIT = 24
_MAX_LIST_LIMIT = 100
_DESCRIPTION_LIMIT = 120
_PAGE_CHAR_LIMIT = 3600
_DEFAULT_BODY_LIMIT = 12_000
_MAX_BODY_LIMIT = 100_000


def _compact_description(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= _DESCRIPTION_LIMIT:
        return text
    return text[: _DESCRIPTION_LIMIT - 1].rstrip() + "…"


def _skill_line(skill: dict[str, Any]) -> str:
    skill_id = str(skill.get("id") or "").strip()
    name = str(skill.get("name") or "").strip()
    source = str(skill.get("source") or "catalog").strip() or "catalog"
    label = skill_id
    if name and name.casefold() != skill_id.casefold():
        label += f": {name}"
    description = _compact_description(skill.get("description"))
    suffix = f" — {description}" if description else ""
    return f"{label} [{source}]{suffix}"


def _paginate_text(text: str, *, offset: int, limit: int) -> tuple[str, bool]:
    total = len(text)
    if total == 0:
        return "", False
    if offset >= total:
        return (
            f"Error: offset {offset} is past end of content ({total} chars). "
            f"Use offset=0..{max(0, total - 1)}."
        ), True
    end = min(offset + limit, total)
    page = text[offset:end]
    if end < total:
        page += (
            f"\n\n… more content after char {end} ({total - end} char(s) left). "
            f"Continue with offset={end}."
        )
    return page, False


def _body_page_args(arguments: dict[str, Any]) -> tuple[int, int] | str:
    try:
        offset = int(arguments.get("offset", 0))
    except (TypeError, ValueError):
        return "Error: 'offset' must be a non-negative integer"
    if offset < 0:
        return "Error: 'offset' must be a non-negative integer"
    try:
        limit = int(arguments.get("limit", _DEFAULT_BODY_LIMIT))
    except (TypeError, ValueError):
        return "Error: 'limit' must be an integer"
    if limit < 1:
        return "Error: 'limit' must be at least 1"
    return offset, min(limit, _MAX_BODY_LIMIT)


def list_skills_run(arguments: dict[str, Any]) -> str:
    """List a compact, paginated skill catalog; always returns a string."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return "Error: list_skills expects a dict of arguments"

    query_value = arguments.get("query", "")
    if query_value is None:
        query_value = ""
    if not isinstance(query_value, str):
        return "Error: 'query' must be a string"
    query = query_value.strip()

    try:
        offset = int(arguments.get("offset", 0))
    except (TypeError, ValueError):
        return "Error: 'offset' must be a non-negative integer"
    if offset < 0:
        return "Error: 'offset' must be a non-negative integer"

    try:
        limit = int(arguments.get("limit", _DEFAULT_LIST_LIMIT))
    except (TypeError, ValueError):
        return "Error: 'limit' must be an integer"
    if limit < 1:
        return "Error: 'limit' must be at least 1"
    limit = min(limit, _MAX_LIST_LIMIT)

    from app.services import store

    try:
        store.sync_skills()
    except Exception:
        pass
    skills = [s for s in store.list_skills() if s.get("enabled", True)]
    if query:
        needle = query.casefold()
        skills = [
            s
            for s in skills
            if needle
            in " ".join(
                str(s.get(field) or "")
                for field in ("id", "name", "description")
            ).casefold()
        ]
    if not skills:
        return f"No skills matched query: {query!r}" if query else "No skills registered"

    total = len(skills)
    if offset >= total:
        return (
            f"No skills at offset {offset}; {total} matching skill(s) available. "
            f"Use offset=0..{total - 1}."
        )

    page: list[str] = []
    start = offset
    used_chars = len(f"Skills {start + 1}-{start + 1} of {total}")
    for skill in skills[offset : offset + limit]:
        line = _skill_line(skill)
        # Keep the catalog page below the normal tool-result budget so this
        # structured response is never cut mid-entry by the agent loop.
        if page and used_chars + len(line) + 1 > _PAGE_CHAR_LIMIT:
            break
        page.append(line)
        used_chars += len(line) + 1

    end = start + len(page)
    lines = [f"Skills {start + 1}-{end} of {total}", *page]
    if end < total:
        hint = f"More skills available. Continue with offset={end} (limit={limit})"
        if query:
            hint += f" for query={query!r}"
        lines.extend(["", hint + "."])
    return "\n".join(lines)


def use_skill_run(arguments: dict[str, Any]) -> str:
    """Return a skill's body (or a support file) from disk; always a string."""
    if not isinstance(arguments, dict):
        return "Error: use_skill expects a dict of arguments"
    skill_id = arguments.get("skill_id") or arguments.get("id") or arguments.get("name")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return "Error: 'skill_id' argument must be a non-empty string"
    skill_id = skill_id.strip()
    page_args = _body_page_args(arguments)
    if isinstance(page_args, str):
        return page_args
    offset, limit = page_args
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
        content, invalid_offset = _paginate_text(content, offset=offset, limit=limit)
        if invalid_offset:
            return content
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
    body_text = body or ((skill or {}).get("description") or "").strip() or "(no body)"
    body_page, invalid_offset = _paginate_text(body_text, offset=offset, limit=limit)
    if invalid_offset:
        return body_page
    parts.append(body_page)
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

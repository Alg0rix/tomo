"""``memory`` tool — curated MEMORY.md / USER.md notes."""

from __future__ import annotations

from typing import Any

from app.runtime.memory import curated
from app.runtime.tools.sandbox import current_agent_id


def _is_self_slug(slug: str, user_id: str) -> bool:
    s = slug.casefold().replace("_", "-")
    uid = (user_id or "").casefold().replace("_", "-")
    return s in {"me", "user", "self", "the-user", "myself", uid} or s.startswith("usr-")


def _vault_index(user_id: str, *, max_pages: int = 40) -> str:
    """Compact list of entity pages with their live facts."""
    from app.runtime.memory.vault import doc, paths

    root = paths.vault_root(user_id) / "entities"
    files = sorted(root.glob("*/*.md")) if root.is_dir() else []
    if not files:
        return "[entity] vault is empty"
    lines = [f"[entity] {len(files)} pages"]
    for path in files[:max_pages]:
        try:
            page = doc.parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        facts = [doc.fact_data(e)["text"] for e in page.entries if not e.startswith("~~")]
        preview = "; ".join(f[:160] for f in facts[:3]) or "(no facts)"
        lines.append(f"  [[{path.parent.name}/{path.stem}]] {preview}")
    if len(files) > max_pages:
        lines.append(f"  … +{len(files) - max_pages} more")
    return "\n".join(lines)


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    action = str(arguments.get("action") or "list").strip().lower()
    target = str(arguments.get("target") or "memory").strip().lower()
    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip() or None
    workplace_id = str(arguments.get("workplace_id") or "").strip() or None

    if target == "entity":
        from app.runtime.memory.vault import paths, write
        from app.runtime.tools.user_ctx import current_user_id

        key = str(arguments.get("entity") or "").strip()
        if action == "list" and not key:
            return _vault_index(current_user_id())
        try:
            typ, slug = paths.entity_key(key)
            if action == "add" and _is_self_slug(slug, current_user_id()):
                return ("Error: don't make a page for the user. Put the fact on the page of the "
                        "thing it is about, e.g. entity=person/max-verstappen with "
                        "\"The user's favorite F1 driver.\" User preferences about how you work go to target=user.")
            if action == "add":
                from datetime import datetime

                # Link the fact to today's timeline page unless a source is given.
                source = str(arguments.get("source") or "").strip() or datetime.now().astimezone().date().isoformat()
                result = write.add_entity(current_user_id(), key, str(arguments.get("content") or ""),
                                          source=source, aliases=arguments.get('aliases'),
                                          supersedes=str(arguments.get('supersedes') or ''))
                return "Saved entity fact." if result["added"] else "Near-duplicate already present."
            if action == "list":
                path = paths.entity_path(current_user_id(), key)
                return path.read_text(encoding="utf-8") if path.is_file() else "Entity is empty."
        except ValueError as exc:
            return f"Error: {exc}"
        return "Error: entity target supports action=add|list only"

    if target == "project":
        from app.runtime.memory import project as project_mem

        if not workplace_id:
            workplace_id = project_mem.workplace_id_for_agent(agent_id)
        if action == "list":
            if not workplace_id:
                return "Error: workplace_id required for project target (agent has no workplace)"
            entries = project_mem.read_entries(workplace_id)
            path = project_mem.project_path(workplace_id)
            lines = [
                f"[project] {path} ({len(entries)} entries)"
                if path
                else "[project] (unavailable)"
            ]
            for i, e in enumerate(entries, 1):
                lines.append(f"  {i}. {e.replace(chr(10), ' ')[:200]}")
            return "\n".join(lines) if entries else f"[project] empty ({path})"
        if action == "add":
            content = arguments.get("content")
            if not isinstance(content, str):
                return "Error: content is required"
            result = project_mem.add_entry(workplace_id, content)
            if not result.get("ok"):
                return f"Error: {result.get('error')}"
            return (
                f"{result.get('message')} "
                f"({result.get('chars', '?')} chars, {result.get('count')} entries)."
            )
        return "Error: project target supports action=add|list only"

    if action == "list":
        if target == "all":
            lines = []
            for t in ("user", "memory"):
                result = curated.list_entries(t, agent_id=agent_id)
                if not result.get("ok"):
                    continue
                lines.append(
                    f"[{t}] {result['path']} "
                    f"({result['chars']}/{result['limit']} chars, {result['count']} entries)"
                )
                for i, e in enumerate(result.get("entries") or [], 1):
                    preview = e.replace("\n", " ")[:120]
                    lines.append(f"  {i}. {preview}")
            from app.runtime.tools.user_ctx import current_user_id

            index_text = _vault_index(current_user_id())
            lines.append(index_text)
            return "\n".join(lines)
        result = curated.list_entries(target, agent_id=agent_id)
        if not result.get("ok"):
            return f"Error: {result.get('error')}"
        lines = [
            f"[{target}] {result['path']} "
            f"({result['chars']}/{result['limit']} chars, {result['count']} entries)"
        ]
        for i, e in enumerate(result.get("entries") or [], 1):
            preview = e.replace("\n", " ")[:200]
            lines.append(f"  {i}. {preview}")
        return "\n".join(lines) if result["count"] else f"[{target}] empty ({result['path']})"

    if action == "add":
        content = arguments.get("content")
        if not isinstance(content, str):
            return "Error: content is required"
        result = curated.add_entry(target, content, agent_id=agent_id)
        if not result.get("ok"):
            return f"Error: {result.get('error')}"
        return (
            f"{result.get('message')} "
            f"({result.get('chars')} chars, {result.get('count')} entries). "
            "Saved to disk; system prompt updates next session."
        )

    if action == "replace":
        old = arguments.get("old") or arguments.get("old_text")
        new = arguments.get("new") or arguments.get("content")
        if not isinstance(old, str) or not isinstance(new, str):
            return "Error: old and new are required"
        result = curated.replace_entry(target, old, new, agent_id=agent_id)
        if not result.get("ok"):
            return f"Error: {result.get('error')}"
        return f"{result.get('message')}. Saved to disk; system prompt updates next session."

    if action == "remove":
        old = arguments.get("old") or arguments.get("old_text") or arguments.get("content")
        if not isinstance(old, str):
            return "Error: old (unique substring) is required"
        result = curated.remove_entry(target, old, agent_id=agent_id)
        if not result.get("ok"):
            return f"Error: {result.get('error')}"
        return f"{result.get('message')}. Saved to disk; system prompt updates next session."

    return "Error: action must be add, replace, remove, or list"


__all__ = ["run"]

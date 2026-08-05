"""``memory`` tool — curated MEMORY.md / USER.md notes."""

from __future__ import annotations

from typing import Any

from app.runtime.memory import curated
from app.runtime.tools.sandbox import current_agent_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    action = str(arguments.get("action") or "list").strip().lower()
    target = str(arguments.get("target") or "memory").strip().lower()
    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip() or None
    workplace_id = str(arguments.get("workplace_id") or "").strip() or None

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
            return "\n".join(lines) if lines else "(empty)"
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

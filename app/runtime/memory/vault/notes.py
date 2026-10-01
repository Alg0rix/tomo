"""Scoped context from vault pages; storage has no character quota."""
from __future__ import annotations

import hashlib
from pathlib import Path
from . import doc, paths

GUIDANCE = (
    "Save durable facts proactively with `memory` to the per-account Markdown vault. "
    "Use entity=type/slug: user/profile for identity and preferences, "
    "agent/<agent-slug> for agent lessons, project/<project-slug> for repo conventions, "
    "and person|tool|place|org|topic pages for facts about things. "
    "Use memory action=list or search before answering recall questions. "
    "Storage has no character quota; prompt excerpts are bounded. "
    "Write declarative facts, skip task progress, and use manage_skill for procedures."
)


def scoped_key(kind: str, identifier: str) -> str:
    # IDs are case-sensitive and may contain characters disallowed in a slug.
    return f"{kind}/id-{hashlib.sha256(identifier.encode()).hexdigest()}"


def facts(user_id: str, key: str, *, home_root: Path | None = None) -> list[str]:
    path = paths.entity_path(user_id, key, home_root=home_root)
    if not path.is_file():
        return []
    page = doc.parse(path.read_text(encoding="utf-8"))
    return [doc.fact_data(e)["text"] for e in page.entries if not e.startswith("~~")]


def context(user_id: str, agent_id: str | None = None, *, home_root: Path | None = None,
            budget: int = 2400) -> str:
    keys = ["user/profile"]
    if agent_id:
        keys.append(scoped_key("agent", agent_id))
        from app.services import store
        agent = store.get_agent(agent_id) or {}
        if agent.get("workplace_id"):
            keys.append(scoped_key("project", agent["workplace_id"]))
    lines = [f"Working notes page: [[{key}]]" for key in keys]
    for key in keys:
        for fact in facts(user_id, key, home_root=home_root):
            lines.append(f"- [[{key}]]: {fact[:500]}")
    return ("Vault profile and working context:\n" + "\n".join(lines))[:budget] if lines else ""

"""Scoped context from vault pages; storage has no character quota."""
from __future__ import annotations

import hashlib
from pathlib import Path
from app.models.ids import slugify
from . import doc, paths

GUIDANCE = (
    "## Memory\n\n"
    "The per-account Markdown vault is your long-term memory. The `Retrieved memory` "
    "block each turn is only a small keyword excerpt: no hit there does not mean the "
    "vault has no answer.\n\n"
    "**Search first.** Call `memory` (action=search, or action=list with an entity) "
    "before you act when the request:\n"
    "- refers to something from the past (\"tadi\", \"kemarin\", \"yang biasa\", "
    "\"remember\") or asks about the user, people, preferences or projects;\n"
    "- concerns a host, service, port, path, account or project whose location is "
    "not already confirmed earlier in this conversation;\n"
    "- will be delegated: put what you found in the brief, and never infer a host "
    "from an agent's name;\n"
    "- is a correction or challenge (\"bukannya di lu?\", \"salah\"): re-check memory "
    "and live state instead of defending your last answer.\n\n"
    "**Query with keywords, not the user's sentence.** Facts are mostly English: "
    "\"relay masih nyala?\" -> query=\"relay cctv proxy 8899\". On a miss, retry once "
    "with synonyms, service or host names, or list the likely entity page. Treat "
    "memory as a lead and verify with tools; when live state disagrees, trust the "
    "live check and fix the fact.\n\n"
    "**Save proactively.** Write durable declarative facts with action=add, one fact "
    "per call, entity=type/slug: user/profile for identity and preferences, "
    "agent/<agent-slug> for agent lessons, project/<project-slug> for repo "
    "conventions, person|tool|place|org|topic for facts about things. Add service "
    "names, unit names, ports and nicknames as aliases so later searches find them. "
    "Skip task progress; use manage_skill for procedures. Storage has no character "
    "quota; prompt excerpts are bounded."
)


def scoped_key(kind: str, identifier: str) -> str:
    # Human-readable, but IDs remain case-sensitive and collision-resistant.
    digest = hashlib.sha256(identifier.encode()).hexdigest()[:8]
    key = f"{kind}/{slugify(identifier)}-{digest}"
    paths.entity_key(key)
    return key


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

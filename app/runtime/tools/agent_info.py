"""``agent_info`` — inspect swarm members' tools, skills, and knowledge."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id


def _resolve_agent(spec: str) -> dict[str, Any] | None:
    from app.services import store

    text = (spec or "").strip()
    if not text:
        return None
    agent = store.get_agent(text)
    if agent:
        return agent
    low = text.lower()
    for a in store.list_agents():
        if (a.get("id") or "").lower() == low:
            return a
        if (a.get("name") or "").strip().lower() == low:
            return a
    return None


def _workplace_brief(agent: dict[str, Any]) -> str:
    from app.services import store

    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        return "workplaces=all"
    if scope == "all_tunnels":
        return "workplaces=all_tunnels"
    ids = list(agent.get("workplace_ids") or [])
    primary = (agent.get("workplace_id") or "").strip()
    if primary and primary not in ids:
        ids = [primary] + ids
    if not ids:
        return "workplace=none"
    labels: list[str] = []
    for wid in ids[:6]:
        w = store.get_workplace(wid)
        if not w:
            labels.append(wid)
            continue
        name = w.get("name") or wid
        kind = (w.get("kind") or "?").strip().lower()
        labels.append(f"{name}/{kind}")
    more = f"+{len(ids) - 6}" if len(ids) > 6 else ""
    return "workplace=" + ",".join(labels) + more


def _list_swarm() -> str:
    from app.services import store

    agents = [a for a in store.list_agents() if a.get("enabled", True)]
    if not agents:
        return "No agents registered."
    lines = ["Swarm members (use agent_info agent=<id|name> for detail):", ""]
    for a in agents:
        aid = a["id"]
        name = a.get("name") or aid
        role = (a.get("role") or "").strip() or "—"
        desc = (a.get("description") or "").strip()
        if len(desc) > 100:
            desc = desc[:97] + "…"
        bits = [
            f"- **{name}** `id={aid}` role={role}",
            _workplace_brief(a),
            f"tools≈{a.get('tool_count', 0)} skills≈{a.get('skill_count', 0)}",
        ]
        if a.get("is_super"):
            bits.append("coordinator")
        lines.append(" · ".join(bits))
        if desc:
            lines.append(f"  {desc}")
    lines.append("")
    lines.append(
        "Shared: knowledge base (remember/recall) and skill catalog are swarm-wide. "
        "Delegate with `delegate` when their role/workplace fits."
    )
    return "\n".join(lines)


def _section_tools(agent_id: str) -> list[str]:
    from app.services import store

    tools = store.get_agent_tools(agent_id)
    on = sorted(
        (t.get("id") or t.get("name") or "")
        for t in tools
        if t.get("enabled", True) and (t.get("id") or t.get("name"))
    )
    lines = [f"Tools ({len(on)} enabled):"]
    if not on:
        lines.append("  (none)")
    else:
        # Compact: wrap ~10 per line
        chunk: list[str] = []
        for name in on:
            chunk.append(name)
            if len(chunk) >= 10:
                lines.append("  " + ", ".join(chunk))
                chunk = []
        if chunk:
            lines.append("  " + ", ".join(chunk))
    return lines


def _section_skills(agent_id: str) -> list[str]:
    from app.services import store

    try:
        store.sync_skills()
    except Exception:
        pass
    rows = store.get_agent_skills(agent_id)
    assigned_ids = sorted(
        (s.get("id") or "").strip()
        for s in rows
        if s.get("assigned") and (s.get("id") or "").strip()
    )
    catalog = [s for s in store.list_skills() if s.get("enabled", True)]
    lines = [
        f"Skills: {len(assigned_ids)} linked to this agent; "
        f"{len(catalog)} enabled in shared catalog."
    ]
    if assigned_ids:
        lines.append("  Linked: " + ", ".join(assigned_ids[:20]))
        if len(assigned_ids) > 20:
            lines.append(f"  … +{len(assigned_ids) - 20} more")
    sample = [s.get("id") or s.get("name") for s in catalog[:12]]
    sample = [x for x in sample if x]
    if sample:
        lines.append("  Catalog sample: " + ", ".join(sample))
        if len(catalog) > 12:
            lines.append(f"  … +{len(catalog) - 12} more (list_skills for full)")
    return lines


def _section_kb() -> list[str]:
    from app.services import store

    entries = store.list_knowledge_entries()
    lines = [
        f"Knowledge base (shared swarm-wide): {len(entries)} entries. "
        "Any agent with recall/remember can search/write."
    ]
    for e in entries[:8]:
        title = (e.get("title") or e.get("id") or "?").strip()
        eid = e.get("id") or ""
        lines.append(f"  - {title} (`{eid}`)")
    if len(entries) > 8:
        lines.append(f"  … +{len(entries) - 8} more (use recall to search)")
    return lines


def _section_memory(agent_id: str) -> list[str]:
    from app.runtime.memory import curated
    from app.services import store

    lines: list[str] = []
    user = curated.list_entries("user", agent_id=agent_id)
    mem = curated.list_entries("memory", agent_id=agent_id)
    lines.append(
        f"Curated memory files: USER.md entries={user.get('count', 0)}; "
        f"agents/{agent_id}/MEMORY.md entries={mem.get('count', 0)} "
        "(frozen into that agent's prompt next session)."
    )
    for label, result in (("USER", user), ("MEMORY", mem)):
        for e in (result.get("entries") or [])[:3]:
            preview = e.replace("\n", " ")[:80]
            lines.append(f"  [{label}] {preview}")
    state = store.list_agent_state(agent_id)
    if state:
        lines.append(f"Agent state keys ({len(state)}): " + ", ".join(sorted(state)[:12]))
        if len(state) > 12:
            lines.append(f"  … +{len(state) - 12} more")
    else:
        lines.append("Agent state: (empty)")
    return lines


def _section_artifacts(agent_id: str) -> list[str]:
    from app.services import store

    all_arts = store.list_artifacts(limit=50) or []
    mine = [h for h in all_arts if (h.get("agent_id") or "") == agent_id][:8]
    lines = [f"Artifacts tagged to this agent: {len(mine)} shown"]
    if not mine:
        lines.append("  (none found)")
        return lines
    for h in mine:
        title = h.get("title") or h.get("path") or h.get("id") or "?"
        lines.append(f"  - {title}")
    return lines


def _detail(agent: dict[str, Any], include: set[str]) -> str:
    aid = agent["id"]
    name = agent.get("name") or aid
    role = (agent.get("role") or "").strip() or "—"
    desc = (agent.get("description") or "").strip()
    lines = [
        f"# {name} (`{aid}`)",
        f"role={role} · enabled={bool(agent.get('enabled', True))} · "
        f"coordinator={bool(agent.get('is_super'))}",
        _workplace_brief(agent),
    ]
    if desc:
        lines.append(desc)
    lines.append("")

    if "tools" in include:
        lines.extend(_section_tools(aid))
        lines.append("")
    if "skills" in include:
        lines.extend(_section_skills(aid))
        lines.append("")
    if "kb" in include or "knowledge" in include:
        lines.extend(_section_kb())
        lines.append("")
    if "memory" in include or "state" in include:
        lines.extend(_section_memory(aid))
        lines.append("")
    if "artifacts" in include:
        lines.extend(_section_artifacts(aid))
        lines.append("")

    lines.append(
        "To hand work off: `delegate` to this agent_id when their tools/workplace fit."
    )
    return "\n".join(lines).rstrip() + "\n"


def run(arguments: dict[str, Any] | None = None) -> str:
    if arguments is not None and not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    args = arguments or {}

    action = str(args.get("action") or "").strip().lower()
    spec = (
        args.get("agent")
        or args.get("agent_id")
        or args.get("name")
        or args.get("id")
        or ""
    )
    if isinstance(spec, str):
        spec = spec.strip()
    else:
        spec = ""

    include_raw = args.get("include")
    if isinstance(include_raw, str) and include_raw.strip():
        include = {p.strip().lower() for p in include_raw.split(",") if p.strip()}
    elif isinstance(include_raw, list):
        include = {str(p).strip().lower() for p in include_raw if str(p).strip()}
    else:
        include = {"tools", "skills", "kb", "memory"}

    # Default action
    if not action:
        action = "get" if spec else "list"
    if action in ("list", "roster", "swarm"):
        return _list_swarm()

    if action in ("get", "info", "show"):
        if not spec:
            # Convenience: info on self
            spec = current_agent_id() or ""
        if not spec:
            return "Error: agent is required (id or name), or use action=list"
        agent = _resolve_agent(spec)
        if not agent:
            return f"Error: unknown agent {spec!r}. Use agent_info action=list."
        return _detail(agent, include)

    return "Error: action must be list or get"


__all__ = ["run"]

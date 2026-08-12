"""Turn digests for the learning reviewer.

Compact structured digest: enough signal for durable distill without
replaying the full transcript into the review client.
"""

from __future__ import annotations

from typing import Any

_MAX_TRAIL_CHARS = 8_000
_MAX_USER_EXCERPTS = 3
_MAX_USER_CHARS = 400
_MAX_FINAL_CHARS = 1_800


def compact_tool_trail(
    messages: list[dict[str, Any]], *, limit: int = _MAX_TRAIL_CHARS
) -> str:
    """Build a compact tool trail from OpenAI-style messages."""
    lines: list[str] = []
    errors = 0
    calls = 0
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict):
                    fn = {}
                name = fn.get("name") or "?"
                args = fn.get("arguments") or ""
                if not isinstance(args, str):
                    args = str(args)
                if len(args) > 200:
                    args = args[:200] + "…"
                lines.append(f"→ {name}({args})")
                calls += 1
        elif role == "tool":
            name = msg.get("name") or "tool"
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            err = content.lstrip().startswith("Error") or "BLOCKED" in content[:40]
            if err:
                errors += 1
            snippet = content.strip().splitlines()[0][:180] if content.strip() else ""
            mark = "✗" if err else "✓"
            lines.append(f"  {mark} {name}: {snippet}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 24] + "\n…(truncated)"
    header = f"[tools={calls} errors={errors}]"
    if not lines:
        return f"{header}\n(no tools)"
    return f"{header}\n{text}"


def _user_excerpts(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        text = content.strip()
        if not text:
            continue
        # Skip synthetic system-ish injections
        if text.startswith("[") and "Active task list" in text[:80]:
            continue
        out.append(text[:_MAX_USER_CHARS])
    return out[-_MAX_USER_EXCERPTS:]


_MAX_CATALOG_CHARS = 2_000
_MAX_USER_SNIPPET = 800
_MAX_CATALOG_SKILLS = 40


def format_skill_catalog(
    skills: list[dict[str, Any]] | None, *, limit: int = _MAX_CATALOG_SKILLS
) -> str:
    """Compact id + description listing for the reviewer (no full bodies)."""
    if not skills:
        return "(empty catalog)"
    lines: list[str] = []
    for s in skills[: max(1, limit)]:
        if not isinstance(s, dict):
            continue
        sid = (s.get("id") or s.get("name") or "").strip()
        if not sid:
            continue
        desc = (s.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 80:
            desc = desc[:77] + "…"
        lines.append(f"- {sid}: {desc}" if desc else f"- {sid}")
    text = "\n".join(lines) if lines else "(empty catalog)"
    if len(text) > _MAX_CATALOG_CHARS:
        text = text[: _MAX_CATALOG_CHARS - 20] + "\n…(truncated)"
    return text


def format_user_snippet(entries: list[str] | None, *, limit: int = _MAX_USER_SNIPPET) -> str:
    cleaned = [e.strip() for e in (entries or []) if (e or "").strip()]
    if not cleaned:
        return "(empty)"
    text = "\n§\n".join(cleaned)
    if len(text) > limit:
        text = text[: limit - 12] + "\n…(truncated)"
    return text


def format_memory_capacity(
    *,
    user_chars: int = 0,
    user_limit: int = 2000,
    user_entries: int = 0,
    agent_chars: int = 0,
    agent_limit: int = 4000,
    agent_entries: int = 0,
) -> str:
    """Tell the reviewer how full curated files are (avoids skill-as-overflow)."""

    def _line(label: str, chars: int, limit: int, n: int) -> str:
        lim = max(1, int(limit or 1))
        pct = min(100, int(100 * max(0, int(chars)) / lim))
        room = max(0, lim - max(0, int(chars)))
        if pct >= 90:
            status = "FULL — replace/remove before add, or use remember/agent_state"
        elif pct >= 70:
            status = "tight — prefer replace over add; remember for long facts"
        else:
            status = "ok"
        return (
            f"- {label}: {chars}/{lim} chars ({pct}%), "
            f"{n} entries, room≈{room} — {status}"
        )

    lines = [
        _line("USER (memory target=user)", user_chars, user_limit, user_entries),
        _line(
            "agent MEMORY (memory target=memory)",
            agent_chars,
            agent_limit,
            agent_entries,
        ),
        "- semantic KB (`remember`): no curated char cap — good overflow for long facts",
        "- agent_state: short key/value facts when files are full",
        "- skills (`manage_skill`): procedures only — NEVER use as memory overflow",
    ]
    return "\n".join(lines)


def build_review_digest(
    *,
    messages: list[dict[str, Any]],
    user_message: str | None,
    final_content: str,
    skills_touched: list[str],
    tool_calls: int = 0,
    plan_reason: str = "",
    skill_catalog: str | None = None,
    user_snippet: str | None = None,
    project_snippet: str | None = None,
    conversation_summary: str | None = None,
    agent_snippet: str | None = None,
    semantic_hint: str | None = None,
    shared_snippet: str | None = None,
    memory_capacity: str | None = None,
) -> str:
    """Structured digest the reviewer consumes as its sole user message body."""
    trail = compact_tool_trail(messages)
    excerpts = _user_excerpts(messages)
    if user_message and user_message.strip():
        goal = user_message.strip()
    elif excerpts:
        goal = excerpts[-1]
    else:
        goal = "(empty)"

    parts = [
        f"## Trigger\n{plan_reason or 'scheduled_review'}",
        f"## Goal (this turn) [conversation]\n{goal[:800]}",
    ]
    if conversation_summary and conversation_summary.strip():
        parts.append(
            "## Conversation summary [conversation]\n"
            + conversation_summary.strip()[:1200]
        )
    if len(excerpts) > 1:
        parts.append(
            "## Recent user messages [conversation]\n"
            + "\n---\n".join(f"- {e}" for e in excerpts[:-1])
        )
    parts.append(f"## Execution trail [execution]\n{trail}")
    parts.append(
        "## Skills touched this turn\n"
        + (", ".join(skills_touched) if skills_touched else "(none)")
    )
    if skills_touched:
        parts.append(
            "## Refine-first\n"
            "These skills were used this turn — call `use_skill` on each before "
            "creating anything new. Prefer `manage_skill` patch over create."
        )
    if skill_catalog is not None:
        parts.append(f"## Existing skill catalog [agent/skills]\n{skill_catalog}")
    if memory_capacity is not None:
        parts.append(f"## Memory capacity\n{memory_capacity}")
    if agent_snippet is not None:
        parts.append(f"## Agent memory [agent]\n{agent_snippet}")
    if user_snippet is not None:
        parts.append(f"## USER profile [user]\n{user_snippet}")
    if project_snippet is not None:
        parts.append(f"## Project notes [project]\n{project_snippet}")
    if semantic_hint is not None:
        parts.append(f"## Semantic KB hint [semantic]\n{semantic_hint}")
    if shared_snippet and shared_snippet.strip():
        parts.append(f"## Shared notes [shared]\n{shared_snippet.strip()}")
    else:
        parts.append(
            "## Shared notes [shared]\n"
            "(Session-scoped swarm_notes from delegate completes; "
            "do not put them in USER.)"
        )
    final = (final_content or "").strip()[:_MAX_FINAL_CHARS] or "(empty)"
    parts.append(f"## Final answer (excerpt) [conversation/diary context]\n{final}")
    parts.append(
        f"## Stats\ntool_calls={tool_calls} messages={len(messages)} "
        f"skills_touched={len(skills_touched)}"
    )
    parts.append(
        "Pick the correct memory lane before writing. "
        "If this turn was a concrete experience, call record_episode with freeform "
        "content (what happened). Diary: line is only a short Companion growth note. "
        "If curated memory is full, replace/remove or use remember — "
        "do not create a skill as overflow. "
        "Act with tools if warranted; otherwise reply exactly: Nothing to save."
    )
    return "\n\n".join(parts)


__all__ = [
    "compact_tool_trail",
    "format_skill_catalog",
    "format_user_snippet",
    "format_memory_capacity",
    "build_review_digest",
]

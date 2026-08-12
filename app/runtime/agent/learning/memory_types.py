"""Canonical memory-OS types for Tomo (SQLite + filesystem lanes).

Nine typed lanes — not one vector store. Skills stay adjacent (workflows).

``diary`` is the learning-review growth line on ``learning_events``.
``episodic`` is concrete past experiences (``episodic_memories``).
"""

from __future__ import annotations

from typing import Any, Literal

MemoryType = Literal[
    "diary",
    "episodic",
    "semantic",
    "user",
    "project",
    "agent",
    "execution",
    "conversation",
    "shared",
]

MEMORY_TYPES: tuple[MemoryType, ...] = (
    "diary",
    "episodic",
    "semantic",
    "user",
    "project",
    "agent",
    "execution",
    "conversation",
    "shared",
)

# Types that count as a durable "saved lesson" when a write succeeds.
SAVED_LESSON_TYPES: frozenset[str] = frozenset(
    {
        "episodic",
        "semantic",
        "user",
        "project",
        "agent",
        "execution",
        "shared",
    }
)

# Tools that can persist durable state during learning review.
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "remember",
        "memory",
        "agent_state",
        "manage_skill",
        "save_artifact",
        "record_episode",
    }
)

READ_TOOLS: frozenset[str] = frozenset(
    {
        "list_skills",
        "use_skill",
        "list_artifacts",
        "recall_episodes",
    }
)

# Tool → default memory type (refined by memory target / args).
_TOOL_DEFAULT_TYPE: dict[str, MemoryType] = {
    "remember": "semantic",
    "agent_state": "agent",
    "manage_skill": "agent",  # skill update adjacent; tagged agent lane for extract
    "save_artifact": "execution",
    "record_episode": "episodic",
    "recall_episodes": "episodic",
    "list_skills": "agent",
    "use_skill": "agent",
    "list_artifacts": "execution",
}

_STORE_HINTS: dict[MemoryType, str] = {
    "diary": "learning_events.diary — short growth-log line for Companion (not full episode)",
    "episodic": "episodic_memories — structured experiences (objective/context/trajectory/outcome/reflection)",
    "semantic": "knowledge_entries via remember/recall (FTS)",
    "user": "$TOMO_HOME/memories/users/<user_id>/USER.md via memory target=user",
    "project": "$TOMO_HOME/workplaces/<id>/PROJECT.md",
    "agent": "agents/<id>/users/<user_id>/MEMORY.md + agent_state",
    "execution": "artifacts + execution_snippets index + tagged review actions",
    "conversation": "messages + session_summaries (session-scoped)",
    "shared": "swarm_notes (session-scoped; published on delegate complete)",
}


def store_hint(memory_type: str) -> str:
    return _STORE_HINTS.get(memory_type, "")  # type: ignore[arg-type]


def memory_type_for_tool(
    tool_name: str,
    *,
    arguments: dict[str, Any] | None = None,
    result_text: str = "",
) -> MemoryType | None:
    """Infer memory type for a review tool call."""
    name = (tool_name or "").strip()
    args = arguments or {}
    if name == "memory":
        target = str(args.get("target") or "memory").strip().lower()
        if target == "user":
            return "user"
        if target == "project":
            return "project"
        return "agent"
    if name == "remember":
        return "semantic"
    if name == "save_artifact":
        return "execution"
    if name == "agent_state":
        return "agent"
    if name == "manage_skill":
        return "agent"
    if name == "record_episode":
        return "episodic"
    if name == "recall_episodes":
        return "episodic"
    if name in READ_TOOLS:
        return _TOOL_DEFAULT_TYPE.get(name)
    return _TOOL_DEFAULT_TYPE.get(name)


def is_successful_write(tool_name: str, result_text: str) -> bool:
    """Whether this tool result is a durable write that should set saved=1."""
    from app.runtime.agent.learning.evaluator import evaluate_write

    return bool(evaluate_write(tool_name, result_text).get("saved_eligible"))


def classify_review_action(
    tool_name: str,
    *,
    arguments: dict[str, Any] | None = None,
    result_text: str = "",
) -> dict[str, Any]:
    """Classify one tool outcome for the growth ledger extract."""
    name = (tool_name or "").strip()
    text = (result_text or "").strip()
    err = text.startswith("Error") or text.startswith("Error:")
    mtype = memory_type_for_tool(name, arguments=arguments, result_text=text)
    write = name in WRITE_TOOLS
    successful_write = is_successful_write(name, text)
    kind = "error" if err else ("write" if write else "read")
    if write and not err and not successful_write:
        kind = "noop"
    return {
        "tool": name,
        "type": mtype,
        "kind": kind,
        "saved_eligible": successful_write,
        "summary": text.splitlines()[0][:140] if text else "",
    }


def classify_actions(
    actions: list[str],
    *,
    classified: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate classification for a review pass.

    Prefer ``classified`` (structured) when available; otherwise parse action
    strings like ``remember: …``.

    Slice 2 extract shape::

        {
          "items": [...],
          "memory_types": [...],
          "saved": true,
          "confidence": 0.9,
        }
    """
    items = list(classified or [])
    if not items:
        for raw in actions or []:
            line = str(raw or "")
            tool, _, rest = line.partition(":")
            tool = tool.strip()
            items.append(
                classify_review_action(tool, result_text=rest.strip())
            )
    writes = [i for i in items if i.get("kind") == "write"]
    reads = [i for i in items if i.get("kind") == "read"]
    errors = [i for i in items if i.get("kind") == "error"]
    noops = [i for i in items if i.get("kind") == "noop"]
    types = sorted(
        {
            str(i.get("type"))
            for i in items
            if i.get("type") and i.get("saved_eligible")
        }
    )
    saved = any(i.get("saved_eligible") for i in items)
    confidence = _extract_confidence(items, saved=saved)
    return {
        "items": items,
        "writes": writes,
        "reads": reads,
        "errors": errors,
        "noops": noops,
        "memory_types": types,
        "saved": saved,
        "confidence": confidence,
    }


def _extract_confidence(items: list[dict[str, Any]], *, saved: bool) -> float:
    """Heuristic confidence for the review extract (0–1)."""
    if not saved:
        return 0.0
    writes = [i for i in items if i.get("saved_eligible")]
    if not writes:
        return 0.0
    # Prefer durable preference/project/semantic/episodic lanes.
    weights = {
        "user": 1.0,
        "project": 0.95,
        "semantic": 0.9,
        "episodic": 0.88,
        "agent": 0.85,
        "execution": 0.75,
        "shared": 0.7,
    }
    scores = [weights.get(str(i.get("type")), 0.7) for i in writes]
    base = sum(scores) / len(scores)
    # Slight boost when multiple successful writes agree.
    if len(writes) >= 2:
        base = min(1.0, base + 0.05)
    # Penalize if there were also errors this pass.
    if any(i.get("kind") == "error" for i in items):
        base = max(0.0, base - 0.1)
    return round(base, 3)


def lanes_prompt_block() -> str:
    """Short rules block for the learning-review system prompt."""
    lines = [
        "Memory lanes (choose the right store — never dump everything into USER):",
        "- user → memory target=user (who they are, prefs, style) — hard char limit",
        "- agent → memory target=memory / agent_state (your notes, env quirks) — hard char limit",
        "- project → memory target=project (stack, architecture for the workplace)",
        "- semantic → remember (searchable general facts; use when files full or fact is long)",
        "- episodic → record_episode (structured experience: objective, context, trajectory, outcome, reflection)",
        "- execution → save_artifact / tool outcomes (not USER prefs)",
        "- diary → Diary: line only (Companion growth log; NOT a full episode)",
        "- conversation → session summary (automatic; do not re-save chat)",
        "- shared → cross-agent session facts (prefer session-visible notes)",
        "Skills (manage_skill) are NOT a memory lane. Only for reusable how-to procedures.",
        "Episodic = what happened this time (experience). Semantic = what is known. Diary = growth log only.",
        "Record failures as well as successes. Prefer trajectory (what was tried) over problem→solution only.",
        "Wrong lane: episode → skill create; prefs → skill; full MEMORY → skill overflow.",
    ]
    return "\n".join(lines)


__all__ = [
    "MEMORY_TYPES",
    "MemoryType",
    "SAVED_LESSON_TYPES",
    "WRITE_TOOLS",
    "READ_TOOLS",
    "store_hint",
    "memory_type_for_tool",
    "is_successful_write",
    "classify_review_action",
    "classify_actions",
    "lanes_prompt_block",
]

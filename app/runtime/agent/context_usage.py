"""Estimate prompt context usage for the chat UI.

Mirrors the agent loop's compression so the displayed token count matches
what actually gets sent to the LLM — no more "161K / 128K" after the
compress step would have reduced it to well under the limit.
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.agent.compress import (
    ContextBudgetError, _DEFAULT_SOFT_LIMIT_TOKENS, _estimate_tokens, _msg_tokens, maybe_compress_messages, prompt_budget,
)
from app.runtime.agent.context import (
    _skills_prompt_section,
    _swarm_agents_prompt_section,
    _workplace_prompt_section,
    build_system_prompt,
    history_to_messages,
)
from app.runtime.llm.context_window import (
    resolve_context_window_sync,
)
from app.services import store

# Segment colors (match Tomo dark theme popover).
_SECTION_META: list[tuple[str, str, str]] = [
    ("system_prompt", "System prompt", "#9ca3af"),
    ("tool_definitions", "Tool definitions", "#a78bfa"),
    ("rules", "Rules", "#4ade80"),
    ("skills", "Skills", "#fb923c"),
    ("workplaces", "Workplaces", "#c4b5fd"),
    ("subagent_definitions", "Subagent definitions", "#38bdf8"),
    ("summarized_conversation", "Summarized conversation", "#f472b6"),
    ("conversation", "Conversation", "#f87171"),
]

# Prefix used by compress.py's summary message (role=="user").
_SUMMARY_PREFIX = "[SYSTEM] Earlier conversation was compressed"


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token)."""
    return _estimate_tokens(text)


def _resolve_context_limit(agent_id: str | None, *, session_id: str | None = None) -> int | None:
    """Use the same route-aware metadata as runtime callers."""
    return resolve_context_window_sync(agent_id, session_id=session_id)


def _system_core_prompt(agent_id: str) -> str:
    """System prompt without swarm roster, workplace, or skills blocks."""
    full = build_system_prompt(agent_id)
    for chunk in (
        _swarm_agents_prompt_section(agent_id),
        _workplace_prompt_section(agent_id),
        _skills_prompt_section(agent_id),
    ):
        if chunk and chunk in full:
            full = full.replace(chunk, "", 1)
    return "\n\n".join(p.strip() for p in full.split("\n\n") if p.strip())


def _rules_text() -> str:
    lines: list[str] = []
    for rule in store.list_safety_rules():
        if not rule.get("enabled", True):
            continue
        name = rule.get("name") or rule.get("id") or "rule"
        pattern = (rule.get("pattern") or "").strip()
        lines.append(f"{name}: {pattern}" if pattern else str(name))
    return "\n".join(lines)


def _skills_text(agent_id: str) -> str:
    lines: list[str] = []
    for sk in store.get_agent_skills(agent_id):
        name = sk.get("name") or sk.get("id") or "skill"
        desc = (sk.get("description") or "").strip()
        lines.append(f"{name}: {desc}" if desc else str(name))
    return "\n".join(lines)


def _tools_text(agent_id: str) -> str:
    try:
        tools = store.get_agent_openai_tools(agent_id)
    except Exception:
        tools = []
    return json.dumps(tools, ensure_ascii=False, separators=(",", ":"))


def _is_summary_message(msg: dict[str, Any]) -> bool:
    """True if *msg* is the compression summary injected by ``compress.py``."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return content.startswith(_SUMMARY_PREFIX)
    return False


def compute_context_usage(
    agent_id: str,
    history: list[dict[str, Any]] | None = None,
    *,
    user_message: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return context budget breakdown for the next turn.

    The conversation token counts reflect what the agent loop *actually*
    sends after ``maybe_compress_messages`` runs, so the UI numbers stay
    consistent with the model's real context window usage.

    Parameters
    ----------
    limit:
        Pre-resolved context window (e.g. from the live ``/models`` API).
        When ``None``, reads cached metadata; unresolved limits stay unknown.
    """
    # ── Build conversation messages the same way the loop does ──
    messages = history_to_messages(history, for_agent_id=agent_id)
    if user_message:
        messages.append({"role": "user", "content": user_message})

    # ── Assemble all sections ──
    counts: dict[str, int] = {
        "system_prompt": estimate_tokens(_system_core_prompt(agent_id)),
        "tool_definitions": estimate_tokens(_tools_text(agent_id)),
        "rules": estimate_tokens(_rules_text()),
        "skills": estimate_tokens(_skills_text(agent_id)),
        "workplaces": estimate_tokens(_workplace_prompt_section(agent_id)),
        "subagent_definitions": estimate_tokens(
            _swarm_agents_prompt_section(agent_id)
        ),
        "summarized_conversation": 0,
        "conversation": 0,
    }

    if limit is None:
        limit = _resolve_context_limit(agent_id, session_id=session_id)
    required = next((msg for msg in reversed(messages) if msg.get("role") == "user"), None)
    required_tokens = _msg_tokens(required) if required is not None else 0
    budget = (prompt_budget(limit) if limit else
              max(_DEFAULT_SOFT_LIMIT_TOKENS, sum(counts.values()) + required_tokens))
    conversation_budget = budget - sum(counts.values())
    compaction_error = None
    try:
        if conversation_budget <= 0:
            raise ContextBudgetError("Instructions and tools exceed the model's prompt budget.")
        # Previewing the next turn may compact a request that already received
        # its final answer. An unanswered/current request must stay intact.
        completed = bool(history and history[-1].get("type") == "final" and not user_message)
        compressed = maybe_compress_messages(messages, soft_limit_tokens=conversation_budget,
                                             protect_latest_user=not completed)
    except ContextBudgetError as exc:
        compressed = messages
        compaction_error = str(exc)
    did_compress = compressed is not messages
    for msg in compressed:
        if msg.get("role") in {"system", "developer"}:
            continue
        section = "summarized_conversation" if _is_summary_message(msg) else "conversation"
        counts[section] += _msg_tokens(msg)
    used = sum(counts.values())

    sections: list[dict[str, Any]] = []
    for sid, label, color in _SECTION_META:
        tokens = counts.get(sid, 0)
        if tokens <= 0:
            continue
        sections.append(
            {
                "id": sid,
                "label": label,
                "tokens": tokens,
                "color": color,
            }
        )

    # Percent: clamped 0-100 for the CSS ring; `used` is unclamped so
    # the token text always tells the truth if somehow still over limit.
    percent = round(100 * used / limit) if limit else 0
    return {
        "agent_id": agent_id,
        "limit": limit,
        "used": used,
        "percent": min(percent, 100),
        "over_limit": used > limit if limit else None,
        "compressed": did_compress,
        "prompt_budget": budget,
        "limit_known": limit is not None,
        "blocked": compaction_error is not None,
        "compaction_error": compaction_error,
        "sections": sections,
    }


__all__ = ["compute_context_usage", "estimate_tokens"]

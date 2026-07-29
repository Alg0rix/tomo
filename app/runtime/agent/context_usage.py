"""Estimate prompt context usage for the chat UI."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.agent.context import (
    _swarm_agents_prompt_section,
    _workplace_prompt_section,
    build_system_prompt,
    history_to_messages,
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

_DEFAULT_CONTEXT = 128_000


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _resolve_context_limit(agent_id: str | None) -> int:
    """Context window for the agent's resolved model profile."""
    try:
        profile = store.resolve_llm_profile(agent_id)
        model_id = (profile or {}).get("model") or ""
    except Exception:
        model_id = ""
    for m in store.list_models():
        mid = m.get("id") or ""
        if mid and (mid == model_id or model_id.startswith(mid)):
            ctx = int(m.get("context") or 0)
            if ctx > 0:
                return ctx
    return _DEFAULT_CONTEXT


def _system_core_prompt(agent_id: str) -> str:
    """System prompt without swarm roster or workplace blocks."""
    full = build_system_prompt(agent_id)
    for chunk in (
        _swarm_agents_prompt_section(agent_id),
        _workplace_prompt_section(agent_id),
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


def _conversation_text(
    history: list[dict[str, Any]] | None,
    *,
    agent_id: str,
    user_message: str | None = None,
) -> str:
    messages = history_to_messages(history, for_agent_id=agent_id)
    if user_message:
        messages.append({"role": "user", "content": user_message})
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role") or ""
        if role == "tool":
            parts.append(f"tool:{msg.get('content') or ''}")
            continue
        if role == "assistant" and msg.get("tool_calls"):
            parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False))
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(f"{role}:{content}")
        elif content is not None:
            parts.append(f"{role}:{json.dumps(content, ensure_ascii=False)}")
    return "\n".join(parts)


def compute_context_usage(
    agent_id: str,
    history: list[dict[str, Any]] | None = None,
    *,
    user_message: str | None = None,
) -> dict[str, Any]:
    """Return context budget breakdown for the next turn."""
    counts = {
        "system_prompt": estimate_tokens(_system_core_prompt(agent_id)),
        "tool_definitions": estimate_tokens(_tools_text(agent_id)),
        "rules": estimate_tokens(_rules_text()),
        "skills": estimate_tokens(_skills_text(agent_id)),
        "workplaces": estimate_tokens(_workplace_prompt_section(agent_id)),
        "subagent_definitions": estimate_tokens(
            _swarm_agents_prompt_section(agent_id)
        ),
        "summarized_conversation": 0,
        "conversation": estimate_tokens(
            _conversation_text(history, agent_id=agent_id, user_message=user_message)
        ),
    }

    limit = _resolve_context_limit(agent_id)
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

    percent = round(100 * used / limit) if limit else 0
    return {
        "agent_id": agent_id,
        "limit": limit,
        "used": used,
        "percent": min(percent, 100),
        "sections": sections,
    }


__all__ = ["compute_context_usage", "estimate_tokens"]

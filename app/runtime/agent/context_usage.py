"""Estimate prompt context usage for the chat UI.

Mirrors the agent loop's compression so the displayed token count matches
what actually gets sent to the LLM — no more "161K / 128K" after the
compress step would have reduced it to well under the limit.
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.agent.compress import maybe_compress_messages
from app.runtime.agent.context import (
    _skills_prompt_section,
    _swarm_agents_prompt_section,
    _workplace_prompt_section,
    build_system_prompt,
    history_to_messages,
)
from app.runtime.llm.context_window import (
    _KNOWN_WINDOWS_SORTED,
    _DEFAULT,
    _agent_model,
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

_DEFAULT_CONTEXT = _DEFAULT

# Prefix used by compress.py's summary message (role=="user").
_SUMMARY_PREFIX = "[SYSTEM] Earlier conversation was compressed"


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _msg_tokens(msg: dict[str, Any]) -> int:
    """Token estimate for a single message — mirrors compress._msg_tokens."""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(str(content))
    if msg.get("tool_calls"):
        parts.append(str(msg["tool_calls"]))
    return estimate_tokens("\n".join(parts))


def _resolve_context_limit(agent_id: str | None) -> int:
    """Context window for the agent's resolved model profile (sync).

    Resolution order (no network):
      1. ``store.list_models()`` seed match (project-specific, most specific)
      2. ``_KNOWN_WINDOWS`` prefix match (longest prefix first)
      3. ``_DEFAULT_CONTEXT``
    """
    model_id = _agent_model(agent_id)
    if not model_id:
        return _DEFAULT_CONTEXT

    # Seed models (user-configured, most specific)
    models = sorted(
        store.list_models(), key=lambda m: len(m.get("id") or ""), reverse=True
    )
    for m in models:
        mid = m.get("id") or ""
        if mid and (mid == model_id or model_id.startswith(mid)):
            ctx = int(m.get("context") or 0)
            if ctx > 0:
                return ctx

    # Known table (prefix match, longest first)
    for prefix, ctx in _KNOWN_WINDOWS_SORTED:
        if model_id.startswith(prefix):
            return ctx

    return _DEFAULT_CONTEXT


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
        When ``None``, falls back to the sync resolver (known table → seed
        → default).
    """
    # ── Build conversation messages the same way the loop does ──
    messages = history_to_messages(history, for_agent_id=agent_id)
    if user_message:
        messages.append({"role": "user", "content": user_message})

    # Run the same compression the agent loop applies before every LLM call.
    compressed = maybe_compress_messages(messages)
    did_compress = compressed is not messages

    # ── Classify compressed conversation messages ──
    summarized_tokens = 0
    conversation_tokens = 0
    for msg in compressed:
        # Skip system messages — they're counted in the system_prompt section.
        if msg.get("role") == "system":
            continue
        tokens = _msg_tokens(msg)
        if _is_summary_message(msg):
            summarized_tokens += tokens
        else:
            conversation_tokens += tokens

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
        "summarized_conversation": summarized_tokens,
        "conversation": conversation_tokens,
    }

    if limit is None:
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

    # Percent: clamped 0-100 for the CSS ring; `used` is unclamped so
    # the token text always tells the truth if somehow still over limit.
    percent = round(100 * used / limit) if limit else 0
    return {
        "agent_id": agent_id,
        "limit": limit,
        "used": used,
        "percent": min(percent, 100),
        "over_limit": used > limit,
        "compressed": did_compress,
        "sections": sections,
    }


__all__ = ["compute_context_usage", "estimate_tokens"]

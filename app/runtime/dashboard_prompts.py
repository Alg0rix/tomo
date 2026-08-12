"""Dynamic Home 'Try asking' chip prompts.

Personalizes the three Home-page prompt chips per account without making
Home depend on an available LLM. ``key``, ``label``, and ``prompt`` are all
model-chosen on the LLM path — there is no fixed category taxonomy. A
process-local, per-user TTL cache avoids re-calling the model on every
Home load; validation failures, timeouts, and unconfigured models all fall
back to a randomized pool of hardcoded suggestions instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

from app.runtime.llm.base import LLMClient

logger = logging.getLogger(__name__)

_KEY_MAX = 40
_LABEL_MAX = 60
_PROMPT_MAX = 300
_KNOWLEDGE_TRUNC = 160
_EPISODE_TRUNC = 160
_KNOWLEDGE_LIMIT = 5
_EPISODE_LIMIT = 5
_CACHE_TTL_S = 90 * 60
_LLM_TIMEOUT_S = 12.0

_SYSTEM = (
    "You suggest three short starter prompts for a multi-agent AI assistant's "
    "home screen, personalized to this user's recent activity below. Reply "
    "with a JSON array only (no markdown fences, no wrapping object) of "
    "exactly 3 objects, each with:\n"
    '- "key": short slug id, unique across the 3\n'
    '- "label": 2-5 word chip label\n'
    '- "prompt": one full sentence starter, unique across the 3\n'
    "Pick 3 distinct, useful categories based on the context given — do not "
    "reuse the same category twice, and do not just restate the context back. "
    "If no context is given, suggest generally useful starters for a "
    "coding/research/planning assistant."
)

# user_id -> (prompts, monotonic_expiry)
_cache: dict[str, tuple[list[dict[str, str]], float]] = {}

_FALLBACK_POOL: list[dict[str, str]] = [
    {
        "key": "plan-project",
        "label": "Plan a project",
        "prompt": "Plan a new project and break it into clear next steps",
    },
    {
        "key": "inspect-codebase",
        "label": "Inspect a codebase",
        "prompt": "Inspect this codebase and summarize the most important risks",
    },
    {
        "key": "research-topic",
        "label": "Research a topic",
        "prompt": "Research this topic and save a concise brief with sources",
    },
    {
        "key": "debug-issue",
        "label": "Debug an issue",
        "prompt": "Help me debug a failing test and explain the root cause",
    },
    {
        "key": "write-tests",
        "label": "Write tests",
        "prompt": "Write tests for the riskiest part of this codebase",
    },
    {
        "key": "review-changes",
        "label": "Review changes",
        "prompt": "Review my latest changes and flag anything risky",
    },
    {
        "key": "draft-docs",
        "label": "Draft docs",
        "prompt": "Draft documentation for a feature I just built",
    },
    {
        "key": "refactor-code",
        "label": "Refactor code",
        "prompt": "Suggest a refactor for a file that has grown too large",
    },
    {
        "key": "summarize-notes",
        "label": "Summarize notes",
        "prompt": "Summarize my recent notes into a short brief",
    },
    {
        "key": "compare-options",
        "label": "Compare options",
        "prompt": "Compare two approaches and recommend one with trade-offs",
    },
    {
        "key": "automate-task",
        "label": "Automate a task",
        "prompt": "Help me automate a repetitive task with a script",
    },
    {
        "key": "explain-error",
        "label": "Explain an error",
        "prompt": "Explain this error message and how to fix it",
    },
]


def _pick_fallback() -> list[dict[str, str]]:
    """3 distinct random entries from the fallback pool."""
    return [dict(e) for e in random.sample(_FALLBACK_POOL, 3)]


def _truncate(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _knowledge_context(user_id: str) -> str:
    from app.services import store

    entries = store.list_knowledge_entries(user_id=user_id)[:_KNOWLEDGE_LIMIT]
    lines = []
    for e in entries:
        title = _truncate(e.get("title") or "", 80)
        body = _truncate(e.get("body") or "", _KNOWLEDGE_TRUNC)
        if not title and not body:
            continue
        tags = ", ".join(e.get("tags") or [])
        bit = f"- {title}: {body}" if title else f"- {body}"
        if tags:
            bit += f" [tags: {tags}]"
        lines.append(bit)
    return "\n".join(lines)


def _episode_context(user_id: str) -> str:
    from app.services import store

    episodes = store.list_episodes(
        user_id=user_id, state="active", limit=_EPISODE_LIMIT
    )
    lines = []
    for ep in episodes:
        objective = _truncate(ep.get("objective") or "", 120)
        ctx = _truncate(ep.get("context_summary") or "", _EPISODE_TRUNC)
        outcome = _truncate(ep.get("outcome_summary") or "", _EPISODE_TRUNC)
        if not objective and not ctx and not outcome:
            continue
        bit = f"- Objective: {objective}" if objective else "- Objective: (none)"
        if ctx:
            bit += f" | Context: {ctx}"
        if outcome:
            bit += f" | Outcome: {outcome}"
        lines.append(bit)
    return "\n".join(lines)


def build_user_context(user_id: str) -> str:
    """Compact, user-scoped context block for the prompt-suggestion LLM call."""
    parts = []
    kn = _knowledge_context(user_id)
    if kn:
        parts.append("Recent knowledge:\n" + kn)
    ep = _episode_context(user_id)
    if ep:
        parts.append("Recent experience:\n" + ep)
    return "\n\n".join(parts)


def _strip_fences(raw: str) -> str:
    s = (raw or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_prompts_json(raw: str | None) -> list[dict[str, str]] | None:
    """Parse+validate model output into exactly 3 distinct prompt items.

    Accepts only a bare JSON array of 3 objects, each with non-empty
    ``key``/``label``/``prompt`` (length-capped), 3 distinct keys
    (case-insensitive) and 3 distinct prompts. Anything else — malformed
    JSON, wrong item count, missing fields, duplicates, an unexpected
    wrapper structure — returns ``None`` so the caller falls back.
    """
    text = _strip_fences(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list) or len(data) != 3:
        return None
    items: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_prompts: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            return None
        key = " ".join(str(item.get("key") or "").split()).strip()[:_KEY_MAX]
        label = " ".join(str(item.get("label") or "").split()).strip()[:_LABEL_MAX]
        prompt = " ".join(str(item.get("prompt") or "").split()).strip()[:_PROMPT_MAX]
        if not key or not label or not prompt:
            return None
        key_norm = key.lower()
        prompt_norm = prompt.lower()
        if key_norm in seen_keys or prompt_norm in seen_prompts:
            return None
        seen_keys.add(key_norm)
        seen_prompts.add(prompt_norm)
        items.append({"key": key, "label": label, "prompt": prompt})
    return items


def _cache_get(user_id: str) -> list[dict[str, str]] | None:
    cached = _cache.get(user_id)
    if cached is None:
        return None
    prompts, expiry = cached
    if time.monotonic() >= expiry:
        del _cache[user_id]
        return None
    return prompts


def _cache_set(user_id: str, prompts: list[dict[str, str]]) -> None:
    _cache[user_id] = (prompts, time.monotonic() + _CACHE_TTL_S)


def clear_dashboard_prompts_cache() -> None:
    """Reset the in-memory cache (for tests)."""
    _cache.clear()


async def _generate(
    user_id: str, *, llm: LLMClient | None = None
) -> list[dict[str, str]] | None:
    try:
        client = llm
        if client is None:
            from app.runtime.llm import get_llm

            client = get_llm()
        ctx = build_user_context(user_id)
        user_content = (ctx or "No prior activity yet.") + "\n\nJSON:"
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ]
        resp = await asyncio.wait_for(
            client.complete(messages, tools=None), timeout=_LLM_TIMEOUT_S
        )
        raw = (resp.content or "").strip()
        parsed = parse_prompts_json(raw)
        logger.info(
            "dashboard prompts LLM raw=%r parsed=%r", raw[:200], parsed
        )
        return parsed
    except Exception as exc:
        logger.warning(
            "dashboard prompts generation failed: %s", exc, exc_info=True
        )
        return None


async def get_dashboard_prompts(
    user_id: str, *, llm: LLMClient | None = None
) -> dict[str, Any]:
    """Resolve dynamic Home prompt chips for *user_id* — cache, LLM, fallback."""
    uid = (user_id or "web").strip() or "web"
    cached = _cache_get(uid)
    if cached is not None:
        return {"prompts": cached, "source": "llm"}
    prompts = await _generate(uid, llm=llm)
    if prompts is not None:
        _cache_set(uid, prompts)
        return {"prompts": prompts, "source": "llm"}
    return {"prompts": _pick_fallback(), "source": "fallback"}


__all__ = [
    "get_dashboard_prompts",
    "parse_prompts_json",
    "build_user_context",
    "clear_dashboard_prompts_cache",
]

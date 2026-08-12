"""Vision (image-input) capability detection for configured LLM profiles.

No live-API capability probe exists (providers don't expose "does this model
accept images" on ``/models``), so resolution is a static, synchronous
prefix-table lookup against the model id — same shape as the static fallback
tier of :mod:`app.runtime.llm.context_window`, minus the network round-trip
and seed-catalog tiers (there is no per-model capability field in
``store.list_models()`` today).

Unknown models default to ``False`` — a turn only ever sends image content
to a model on the known-vision list, never a guess.
"""

from __future__ import annotations

from typing import Any

# Prefix -> vision-capable. Longest-prefix-first match (see
# ``_KNOWN_VISION_SORTED``). Researched August 2026; update as new model
# families ship. Deliberately conservative — omitting a real vision model
# only costs a text fallback note, never a broken request.
_KNOWN_VISION_PREFIXES: list[str] = [
    # OpenAI — GPT-4o/4.1/4.5, the GPT-5 family (incl. 5.x/mini/nano/sol/
    # terra/luna variants), and the o-series reasoning models all take image
    # input.
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    # Anthropic — every Claude 3+ model ships vision (Claude 3 was the first
    # vision-capable Claude generation); this covers 3.x, Sonnet/Opus/Haiku
    # 4.x, Opus 5, Sonnet 5, and the Fable/Mythos-class models.
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
    # Google — Gemini has been natively multimodal since 1.5; Gemini 3 /
    # Omni made every modality co-equal.
    "gemini-1.5",
    "gemini-2.",
    "gemini-3",
    "gemini-omni",
    # Mistral's vision line (base mistral-large/small text models are not
    # vision-capable and are intentionally not listed).
    "pixtral",
    # Alibaba Qwen vision-language line (plain "qwen" text models excluded).
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen-vl",
    # Meta Llama vision (3.2+ added vision variants; Llama 4 is natively
    # multimodal). Llama 3.1 and earlier are text-only and excluded.
    "llama-3.2-vision",
    "llama-4",
    # Google Gemma vision (3+; Gemma 2 and earlier are text-only).
    "gemma-3",
    # DeepSeek's vision-language variant — the plain deepseek-chat /
    # deepseek-reasoner models are text-only and intentionally excluded.
    "deepseek-vl",
]

_KNOWN_VISION_SORTED = sorted(_KNOWN_VISION_PREFIXES, key=len, reverse=True)


def model_supports_vision(model_id: str | None) -> bool:
    """True when *model_id* is known to accept image input."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    for prefix in _KNOWN_VISION_SORTED:
        if mid.startswith(prefix):
            return True
    return False


def resolve_model_id(agent_id: str | None) -> str:
    """Best-effort model id for *agent_id*'s resolved LLM profile."""
    try:
        from app.services import store

        profile: dict[str, Any] | None = store.resolve_llm_profile(agent_id)
        return ((profile or {}).get("model") or "").strip()
    except Exception:
        return ""


def agent_supports_vision(agent_id: str | None) -> bool:
    """True when *agent_id* is currently configured with a vision-capable model."""
    return model_supports_vision(resolve_model_id(agent_id))


__all__ = [
    "model_supports_vision",
    "resolve_model_id",
    "agent_supports_vision",
]

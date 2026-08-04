"""Resolve model context window: provider API → seed → known table → default.

Central async resolver with TTL cache.  Used by the context-usage API
endpoints so the UI shows the real provider context when available.

Resolution order:
  1. In-memory cache (TTL 1 h)
  2. ``OpenAICompatClient.fetch_model_context_window()`` (live /models API)
  3. ``store.list_models()`` seed data (project-specific, user-configured)
  4. ``_KNOWN_WINDOWS`` prefix match (static fallback)
  5. ``_DEFAULT`` (128 000)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.runtime.llm.openai_compat import extract_context_window

_logger = logging.getLogger(__name__)

_DEFAULT = 128_000
_CACHE_TTL_S = 3600.0

# (base_url, model) → (context_limit, monotonic_expiry)
_cache: dict[tuple[str, str], tuple[int, float]] = {}

# Known windows for providers that don't expose context on /models.
# Matched by prefix (longest prefix first).
_KNOWN_WINDOWS: list[tuple[str, int]] = [
    ("gpt-4.1-mini", 1_047_576),
    ("gpt-4.1-nano", 1_047_576),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8192),
    ("gpt-3.5-turbo-16k", 16_385),
    ("gpt-3.5-turbo", 16_385),
    ("o3-mini", 200_000),
    ("o3", 200_000),
    ("o1-mini", 128_000),
    ("o1", 200_000),
    ("claude-3-7-sonnet", 200_000),
    ("claude-3-5-sonnet", 200_000),
    ("claude-3-5-haiku", 200_000),
    ("claude-3-opus", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-opus-4", 200_000),
    ("deepseek-chat", 128_000),
    ("deepseek-reasoner", 128_000),
    ("gemini-2.0-flash", 1_048_576),
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
]

# Pre-sorted longest-prefix-first for matching.
_KNOWN_WINDOWS_SORTED = sorted(_KNOWN_WINDOWS, key=lambda t: len(t[0]), reverse=True)


def _lookup_known(model_id: str) -> int | None:
    """Prefix-match *model_id* against the known-windows table."""
    for prefix, ctx in _KNOWN_WINDOWS_SORTED:
        if model_id.startswith(prefix):
            return ctx
    return None


def _resolve_seed(model_id: str) -> int | None:
    """Look up *model_id* in the platform seed models (``store.list_models``)."""
    try:
        from app.services import store

        models = sorted(
            store.list_models(),
            key=lambda m: len(m.get("id") or ""),
            reverse=True,
        )
        for m in models:
            mid = m.get("id") or ""
            if mid and (mid == model_id or model_id.startswith(mid)):
                ctx = int(m.get("context") or 0)
                if ctx > 0:
                    return ctx
    except Exception:
        pass
    return None


def resolve_context_window_sync(agent_id: str | None = None) -> int:
    """Sync fallback: seed → known table → default (no network).

    Used when the async path is unavailable (e.g. tests, sync callers).
    """
    model_id = _agent_model(agent_id)
    if model_id:
        ctx = _resolve_seed(model_id)
        if ctx is not None:
            return ctx
        ctx = _lookup_known(model_id)
        if ctx is not None:
            return ctx
    return _DEFAULT


async def resolve_context_window(agent_id: str | None = None) -> int:
    """Resolve context window for the agent's LLM profile.

    1. Cache hit (base_url, model)
    2. ``OpenAICompatClient.fetch_model_context_window()``
    3. ``store.list_models()`` seed match (project-specific)
    4. ``_KNOWN_WINDOWS`` prefix match (static fallback)
    5. ``_DEFAULT``
    """
    from app.runtime.llm.openai_compat import LLMConfigError, OpenAICompatClient

    profile = _get_profile(agent_id)
    base_url = (profile or {}).get("base_url") or "https://api.openai.com/v1"
    model_id = (profile or {}).get("model") or ""
    base_url = base_url.rstrip("/")

    if not model_id:
        return _DEFAULT

    # 1. Cache hit
    cache_key = (base_url, model_id)
    cached = _cache.get(cache_key)
    if cached is not None:
        ctx, expiry = cached
        if time.monotonic() < expiry:
            return ctx
        del _cache[cache_key]

    # 2. Live /models API
    result: int | None = None
    try:
        client = OpenAICompatClient(
            base_url=base_url,
            api_key=(profile or {}).get("api_key") or "",
            model=model_id,
        )
        try:
            result = await client.fetch_model_context_window()
        finally:
            await client.aclose()
    except LLMConfigError:
        _logger.debug("no API key; skipping provider context lookup")
    except Exception as exc:
        _logger.info("provider context lookup failed: %s", exc)

    # 3. Seed (project-specific, user-configured)
    if result is None:
        result = _resolve_seed(model_id)

    # 4. Known table (static fallback)
    if result is None:
        result = _lookup_known(model_id)

    # 5. Default
    if result is None:
        result = _DEFAULT

    # Cache
    _cache[cache_key] = (result, time.monotonic() + _CACHE_TTL_S)
    return result


def clear_context_window_cache() -> None:
    """Reset the in-memory cache (for tests)."""
    _cache.clear()


# ── internal helpers ──────────────────────────────────────────────


def _get_profile(agent_id: str | None) -> dict[str, Any] | None:
    try:
        from app.services import store

        return store.resolve_llm_profile(agent_id)
    except Exception:
        return None


def _agent_model(agent_id: str | None) -> str:
    profile = _get_profile(agent_id)
    return ((profile or {}).get("model") or "").strip()


__all__ = [
    "resolve_context_window",
    "resolve_context_window_sync",
    "clear_context_window_cache",
    "extract_context_window",
    "_DEFAULT",
    "_KNOWN_WINDOWS",
]

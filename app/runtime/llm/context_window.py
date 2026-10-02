"""Resolve model context window: provider API → seed → catalog → known table → default.

Central async resolver with TTL cache.  Used by the context-usage API
endpoints so the UI shows the real provider context when available.

Resolution order:
  1. In-memory cache (TTL 1 h)
  2. ``OpenAICompatClient.fetch_model_context_window()`` (live /models API)
  3. ``store.list_models()`` seed data (project-specific, user-configured)
  4. Public model catalog for models whose proxy omits context metadata
  5. ``_KNOWN_WINDOWS`` prefix match (static fallback)
  6. ``_DEFAULT`` (128 000)
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx2

from app.runtime.llm.codex_models import _extract_chatgpt_account_id
from app.runtime.llm.openai_compat import extract_context_window

_logger = logging.getLogger(__name__)

_DEFAULT = 128_000
_CACHE_TTL_S = 3600.0
_CATALOG_TTL_S = 4 * 3600.0
_CATALOG_RETRY_S = 300.0
_CATALOG_URL = "https://models.dev/api.json"

# (base_url, model, credential fingerprint) → (context_limit, monotonic_expiry)
_cache: dict[tuple[str, str, str], tuple[int, float]] = {}
_catalog: dict[str, dict[str, int]] = {}
_catalog_expiry = 0.0

# ChatGPT Codex advertises a smaller route-specific window than the direct API.
# Used only when its authenticated catalog cannot be reached.
_CODEX_WINDOWS: list[tuple[str, int]] = [
    ("gpt-5.3-codex-spark", 128_000),
    ("gpt-6-", 272_000),
    ("gpt-5.6-", 272_000),
    ("gpt-5.5", 272_000),
    ("gpt-5.4", 272_000),
    ("gpt-5.3-codex", 272_000),
    ("gpt-5.2-codex", 272_000),
    ("gpt-5.1-codex", 272_000),
    ("gpt-5-codex", 272_000),
    ("gpt-5", 272_000),
]

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
    ("deepseek-chat", 1_000_000),
    ("deepseek-reasoner", 1_000_000),
    ("deepseek-flash", 1_000_000),
    ("deepseek-v4-flash", 1_000_000),
    ("deepseek-v4-pro", 1_000_000),
    ("gemini-2.0-flash", 1_048_576),
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
]

# Pre-sorted longest-prefix-first for matching.
_KNOWN_WINDOWS_SORTED = sorted(_KNOWN_WINDOWS, key=lambda t: len(t[0]), reverse=True)


def _lookup_known(model_id: str) -> int | None:
    """Prefix-match *model_id* against the known-windows table."""
    model_id = _canonical_model_id(model_id)
    for prefix, ctx in _KNOWN_WINDOWS_SORTED:
        if model_id.startswith(prefix):
            return ctx
    return None


def _canonical_model_id(model_id: str) -> str:
    """Drop a proxy namespace such as ``cline-pass/`` or ``openai/``."""
    return model_id.rsplit("/", 1)[-1].lstrip("~").strip()


def _lookup_codex(model_id: str) -> int | None:
    model = _canonical_model_id(model_id)
    for prefix, ctx in _CODEX_WINDOWS:
        if model.startswith(prefix):
            return ctx
    return None


async def _fetch_codex_context(profile: dict[str, Any], model_id: str) -> int | None:
    """Use the authenticated Codex catalog, whose limits are route-specific."""
    token = profile.get("access_token") or ""
    base_url = (profile.get("base_url") or "").rstrip("/")
    if not token or not base_url:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    account_id = _extract_chatgpt_account_id(token)
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    model = _canonical_model_id(model_id)
    try:
        async with httpx2.AsyncClient(timeout=8.0) as client:
            for version in ("99.0.0", "0.0.0"):
                response = await client.get(
                    base_url + "/models", params={"client_version": version}, headers=headers
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
                entries = payload.get("models") if isinstance(payload, dict) else None
                if not isinstance(entries, list) or not entries:
                    continue
                for item in entries:
                    if isinstance(item, dict) and item.get("slug") == model:
                        return extract_context_window(item)
                break
    except Exception as exc:
        _logger.debug("Codex context catalog unavailable: %s", exc)
    return None


def _catalog_context(catalog: dict[str, dict[str, int]], model_id: str) -> int | None:
    """Use an exact catalog model, preferring its original model provider."""
    model = _canonical_model_id(model_id)
    if not model:
        return None
    provider = (
        "openai" if model.startswith(("gpt-", "o1", "o3", "o4")) else
        "anthropic" if model.startswith("claude-") else
        "deepseek" if model.startswith("deepseek-") else ""
    )
    if provider:
        found = catalog.get(provider, {}).get(model)
        if found is not None:
            return found
    matches = {models[model] for models in catalog.values() if model in models}
    return next(iter(matches)) if len(matches) == 1 else None


async def _fetch_public_catalog_context(model_id: str) -> int | None:
    """Look up exact model metadata without sending credentials or prompts."""
    global _catalog, _catalog_expiry
    now = time.monotonic()
    if now >= _catalog_expiry:
        try:
            async with httpx2.AsyncClient(timeout=8.0) as client:
                response = await client.get(_CATALOG_URL)
                response.raise_for_status()
                payload = response.json()
            catalog: dict[str, dict[str, int]] = {}
            if isinstance(payload, dict):
                for provider_id, provider in payload.items():
                    if not isinstance(provider, dict):
                        continue
                    models = provider.get("models")
                    if not isinstance(models, dict):
                        continue
                    known = {}
                    for mid, info in models.items():
                        if not isinstance(info, dict):
                            continue
                        limit = info.get("limit")
                        if not isinstance(limit, dict):
                            continue
                        ctx = extract_context_window({"context_window": limit.get("context")})
                        if ctx is not None:
                            known[mid] = ctx
                    catalog[provider_id] = known
            if not any(catalog.values()):
                raise ValueError("empty public model catalog")
            _catalog = catalog
            _catalog_expiry = now + _CATALOG_TTL_S
        except Exception as exc:
            _logger.debug("public model catalog unavailable: %s", exc)
            _catalog_expiry = now + _CATALOG_RETRY_S
    return _catalog_context(_catalog, model_id)


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
    profile = _get_profile(agent_id)
    model_id = ((profile or {}).get("model") or "").strip()
    if model_id:
        if (profile or {}).get("auth_mode") == "subscription":
            return _lookup_codex(model_id) or _DEFAULT
        ctx = _resolve_seed(model_id)
        if ctx is not None:
            return ctx
        ctx = _lookup_known(model_id)
        if ctx is not None:
            return ctx
    return _DEFAULT


async def resolve_context_window(agent_id: str | None = None, *, session_id: str | None = None) -> int:
    """Resolve context window for the agent's LLM profile.

    1. Cache hit (base_url, model, credential)
    2. Codex route catalog or ``OpenAICompatClient.fetch_model_context_window()``
    3. ``store.list_models()`` seed match (project-specific)
    4. Public model catalog exact match
    5. ``_KNOWN_WINDOWS`` prefix match (static fallback)
    6. ``_DEFAULT``
    """
    from app.runtime.llm.openai_compat import LLMConfigError, OpenAICompatClient

    profile = _get_profile(agent_id, session_id=session_id)
    base_url = (profile or {}).get("base_url") or "https://api.openai.com/v1"
    model_id = (profile or {}).get("model") or ""
    base_url = base_url.rstrip("/")

    if not model_id:
        return _DEFAULT

    credential = (profile or {}).get("access_token") if (profile or {}).get("auth_mode") == "subscription" else (profile or {}).get("api_key")
    fingerprint = hashlib.sha256((credential or "").encode()).hexdigest()[:16]
    cache_key = (base_url, model_id, fingerprint)
    cached = _cache.get(cache_key)
    if cached is not None:
        ctx, expiry = cached
        if time.monotonic() < expiry:
            return ctx
        del _cache[cache_key]

    if (profile or {}).get("auth_mode") == "subscription":
        result = await _fetch_codex_context(profile, model_id)
        ttl = _CACHE_TTL_S if result is not None else _CATALOG_RETRY_S
        result = result or _lookup_codex(model_id) or _DEFAULT
        _cache[cache_key] = (result, time.monotonic() + ttl)
        return result

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

    # 4. Public catalog (proxies often list models without context metadata).
    if result is None:
        result = await _fetch_public_catalog_context(model_id)

    # 5. Known table when the public catalog is unavailable or lacks this model.
    if result is None:
        result = _lookup_known(model_id)

    # 6. Default
    if result is None:
        result = _DEFAULT

    # Cache
    _cache[cache_key] = (result, time.monotonic() + _CACHE_TTL_S)
    return result


def clear_context_window_cache() -> None:
    """Reset the in-memory cache (for tests)."""
    global _catalog_expiry
    _cache.clear()
    _catalog.clear()
    _catalog_expiry = 0.0


# ── internal helpers ──────────────────────────────────────────────


def _get_profile(agent_id: str | None, *, session_id: str | None = None) -> dict[str, Any] | None:
    try:
        from app.runtime.llm import resolve_main_profile

        return resolve_main_profile(agent_id, session_id=session_id)
    except Exception:
        return None


def _agent_model(agent_id: str | None, *, session_id: str | None = None) -> str:
    profile = _get_profile(agent_id, session_id=session_id)
    return ((profile or {}).get("model") or "").strip()


__all__ = [
    "resolve_context_window",
    "resolve_context_window_sync",
    "clear_context_window_cache",
    "extract_context_window",
    "_DEFAULT",
    "_KNOWN_WINDOWS",
]

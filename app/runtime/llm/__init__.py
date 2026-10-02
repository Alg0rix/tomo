"""LLM client factory and public re-exports.

``get_llm(agent_id=None)`` builds an :class:`OpenAICompatClient` from the
resolved LLM profile (Alpha §2.2): the agent's assigned profile → the default
profile → the first enabled profile. There is no mock provider in the product
path — tests inject :class:`MockLLMClient` into ``run_turn``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.llm.codex_responses import CodexResponsesClient
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    OpenAICompatClient,
    default_llm_timeout_seconds,
    format_llm_error,
)


def get_llm(
    agent_id: str | None = None, reasoning_effort: str | None = None, *, profile_id: str | None = None,
    session_id: str | None = None, model: str | None = None,
) -> LLMClient:
    """Return an OpenAI-compatible client resolved from LLM profiles.

    Resolution (Alpha §2.2): the agent's assigned profile (if set and enabled)
    → ``default_model_id`` → the first enabled profile. Raises
    :class:`LLMConfigError` when no usable profile exists (or the resolved
    profile has no API key).
    """
    from app.services import store
    from app.models.mixins.llm_profiles import effective_reasoning_effort

    if profile_id:
        from app.models.mixins.llm_profiles import get_profile, _maybe_refresh_subscription

        def selected(conn):
            value = get_profile(conn, profile_id)
            return _maybe_refresh_subscription(conn, value) if value else None

        profile = store.with_db(selected)
        if not profile or not profile.get("enabled"):
            raise LLMConfigError("Selected model profile is unavailable")
    elif session_id:
        profile = store.resolve_session_llm_profile(session_id, agent_id)
    else:
        profile = store.resolve_llm_profile(agent_id)
    if not profile:
        raise LLMConfigError("Configure a model profile in System → Models")
    if profile.get("needs_reauth"):
        raise LLMConfigError(
            "ChatGPT sign-in expired — reconnect in System → Models"
        )
    if model:
        allowed = profile.get("available_models") or [profile.get("model")]
        if model not in allowed:
            raise LLMConfigError("Selected model is not in the profile's catalog")
        profile = {**profile, "model": model}
    effective_effort = effective_reasoning_effort(profile, reasoning_effort)

    def configured(client):
        # Freeze the actual route/model so a mid-turn picker change cannot
        # resolve a context window for a different client.
        client.context_profile = dict(profile)
        return client

    if profile.get("auth_mode") == "subscription":
        return configured(CodexResponsesClient(
            base_url=profile.get("base_url") or "",
            access_token=profile.get("access_token") or "",
            model=profile.get("model") or "gpt-5-codex",
            reasoning_effort=effective_effort,
            timeout=default_llm_timeout_seconds(),
        ))
    base_url = (profile.get("base_url") or "").strip() or "https://api.openai.com/v1"
    model = (profile.get("model") or "").strip() or "gpt-4o-mini"
    from app.runtime.llm.provider_catalog import model_protocol, provider_for_url

    provider = provider_for_url(base_url)
    protocol = model_protocol(provider, model) if provider else "chat"
    if protocol in ("messages", "google"):
        from app.runtime.llm.native import NativeMessagesClient

        return configured(NativeMessagesClient(
            base_url=base_url, api_key=profile.get("api_key") or "", model=model,
            protocol=protocol, timeout=default_llm_timeout_seconds(),
        ))
    if protocol == "responses" or urlparse(base_url).hostname == "api.openai.com":
        return configured(CodexResponsesClient(
            base_url=base_url,
            access_token=profile.get("api_key") or "",
            model=model,
            reasoning_effort=effective_effort,
            timeout=default_llm_timeout_seconds(),
        ))
    # OpenAICompatClient raises LLMConfigError when the API key is empty.
    return configured(OpenAICompatClient(
        base_url=base_url,
        api_key=profile.get("api_key") or "",
        model=model,
        reasoning_effort=effective_effort,
        timeout=default_llm_timeout_seconds(),
    ))


def resolve_main_profile(agent_id: str | None = None, *, session_id: str | None = None) -> dict | None:
    """Resolve the main model in an active chat context, or the global default."""
    from app.runtime.artifacts.fs import current_session_id
    from app.services import store

    sid = session_id or current_session_id()
    return store.resolve_session_llm_profile(sid, agent_id) if sid else store.resolve_llm_profile(agent_id)


def get_auxiliary_llm(task: str, *, agent_id: str | None = None, session_id: str | None = None) -> LLMClient:
    """An explicit task override, otherwise the main chat/global model."""
    from app.services import store

    settings = store.get_settings()
    profile_id = str(settings.get(task + "_profile_id") or "").strip()
    model = str(settings.get(task + "_model_name") or "").strip() if profile_id else None
    options = {}
    if profile_id:
        options['profile_id'] = profile_id
        if model:
            options['model'] = model
    elif session_id:
        options['session_id'] = session_id
        effort = store.resolve_session_reasoning_effort(session_id, agent_id)
        if effort:
            options['reasoning_effort'] = effort
    return get_llm(agent_id, **options)


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatClient",
    "CodexResponsesClient",
    "LLMConfigError",
    "LLMRequestError",
    "format_llm_error",
    "default_llm_timeout_seconds",
    "get_llm",
    "get_auxiliary_llm",
    "resolve_main_profile",
]

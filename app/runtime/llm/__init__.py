"""LLM client factory and public re-exports.

``get_llm()`` builds an :class:`OpenAICompatClient` from SQLite settings
(``llm_base_url``, ``llm_api_key``, ``llm_model``). There is no mock provider
in the product path — tests inject :class:`MockLLMClient` into ``run_turn``.
"""

from __future__ import annotations

from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.openai_compat import LLMConfigError, OpenAICompatClient


def get_llm() -> LLMClient:
    """Return an OpenAI-compatible client from System settings.

    Raises :class:`LLMConfigError` when the API key is not configured.
    """
    from app.services import store

    settings = store.get_settings()
    api_key = str(settings.get("llm_api_key") or "").strip()
    if not api_key:
        raise LLMConfigError(
            "Configure LLM in System → Models (API key required)."
        )
    base_url = str(settings.get("llm_base_url") or "").strip() or "https://api.openai.com/v1"
    model = str(settings.get("llm_model") or settings.get("default_model") or "gpt-4o-mini")
    return OpenAICompatClient(base_url=base_url, api_key=api_key, model=model)


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatClient",
    "LLMConfigError",
    "get_llm",
]

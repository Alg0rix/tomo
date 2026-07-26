"""LLM client factory and public re-exports.

``get_llm(agent_id=None)`` builds an :class:`OpenAICompatClient` from the
resolved LLM profile (Alpha §2.2): the agent's assigned profile → the default
profile → the first enabled profile. There is no mock provider in the product
path — tests inject :class:`MockLLMClient` into ``run_turn``.
"""

from __future__ import annotations

from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.openai_compat import LLMConfigError, OpenAICompatClient


def get_llm(agent_id: str | None = None) -> LLMClient:
    """Return an OpenAI-compatible client resolved from LLM profiles.

    Resolution (Alpha §2.2): the agent's assigned profile (if set and enabled)
    → ``default_model_id`` → the first enabled profile. Raises
    :class:`LLMConfigError` when no usable profile exists (or the resolved
    profile has no API key).
    """
    from app.services import store

    profile = store.resolve_llm_profile(agent_id)
    if not profile:
        raise LLMConfigError("Configure a model profile in System → Models")
    base_url = (profile.get("base_url") or "").strip() or "https://api.openai.com/v1"
    model = (profile.get("model") or "").strip() or "gpt-4o-mini"
    # OpenAICompatClient raises LLMConfigError when the API key is empty.
    return OpenAICompatClient(
        base_url=base_url, api_key=profile.get("api_key") or "", model=model
    )


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatClient",
    "LLMConfigError",
    "get_llm",
]

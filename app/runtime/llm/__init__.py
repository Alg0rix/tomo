"""LLM client factory and public re-exports.

``get_llm()`` returns a client implementing :class:`LLMClient` based on
``TOMO_LLM_PROVIDER``:

* ``mock`` (default) -> :class:`MockLLMClient` — no network, deterministic.
* ``openai_compat`` -> :class:`OpenAICompatClient` — real HTTP via httpx.

Unknown providers raise :class:`LLMProviderError` so misconfiguration
fails fast. Configuration is read lazily from :mod:`app.core.config` at
call time so tests can monkeypatch env-derived values.
"""

from __future__ import annotations

from app.core import config
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.openai_compat import OpenAICompatClient


class LLMProviderError(RuntimeError):
    """Raised when ``TOMO_LLM_PROVIDER`` is not a recognised value."""


def get_llm() -> LLMClient:
    """Return the LLM client selected by ``TOMO_LLM_PROVIDER``."""
    provider = (config.LLM_PROVIDER or "").strip().lower()
    if provider == "mock":
        return MockLLMClient()
    if provider == "openai_compat":
        return OpenAICompatClient()
    raise LLMProviderError(
        f"Unknown TOMO_LLM_PROVIDER={config.LLM_PROVIDER!r}; "
        "expected 'mock' or 'openai_compat'."
    )


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatClient",
    "get_llm",
    "LLMProviderError",
]

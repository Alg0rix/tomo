"""``get_llm()`` factory tests across providers and misconfiguration."""

from __future__ import annotations

import pytest

from app.core import config
from app.runtime.llm import (
    LLMProviderError,
    MockLLMClient,
    OpenAICompatClient,
    get_llm,
)
from app.runtime.llm.openai_compat import LLMConfigError


def test_default_provider_returns_mock(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    client = get_llm()
    assert isinstance(client, MockLLMClient)


def test_openai_compat_provider_returns_client(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-4o-mini")
    client = get_llm()
    assert isinstance(client, OpenAICompatClient)


def test_openai_compat_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(LLMConfigError):
        get_llm()


def test_unknown_provider_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "bogus")
    with pytest.raises(LLMProviderError):
        get_llm()


def test_provider_is_case_insensitive(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "MOCK")
    assert isinstance(get_llm(), MockLLMClient)

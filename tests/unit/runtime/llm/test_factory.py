"""``get_llm()`` factory — settings-backed OpenAI-compatible client only."""

from __future__ import annotations

import pytest

from app.runtime.llm import OpenAICompatClient, get_llm
from app.runtime.llm.openai_compat import LLMConfigError
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "llm-factory.db")


def test_get_llm_raises_without_api_key(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(LLMConfigError, match="System"):
        get_llm()


def test_get_llm_builds_client_from_settings(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({
        "llm_api_key": "sk-test",
        "llm_base_url": "https://example.test/v1",
        "llm_model": "gpt-test",
    })
    client = get_llm()
    assert isinstance(client, OpenAICompatClient)
    assert client.endpoint == "https://example.test/v1/chat/completions"

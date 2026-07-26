"""``get_llm()`` factory — settings-backed OpenAI-compatible client only."""

from __future__ import annotations

import pytest

from app.runtime.llm import OpenAICompatClient, get_llm
from app.runtime.llm.openai_compat import LLMConfigError
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "llm-factory.db")


def test_get_llm_raises_without_profile(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(LLMConfigError, match="System"):
        get_llm()


def _profile(pid: str, base: str, model: str) -> dict:
    return {"id": pid, "name": pid, "base_url": base, "api_key": "sk-" + pid, "model": model}


def test_get_llm_builds_client_from_default_profile(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://example.test/v1", "gpt-test"))
    store.set_default_llm_profile("default")
    client = get_llm()
    assert isinstance(client, OpenAICompatClient)
    assert client.endpoint == "https://example.test/v1/chat/completions"

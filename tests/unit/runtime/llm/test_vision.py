"""Vision (image-input) capability detection."""

from __future__ import annotations

from app.runtime.llm.vision import (
    agent_supports_vision,
    model_supports_vision,
    resolve_model_id,
)
from app.services import store


def test_known_vision_models_by_prefix() -> None:
    for model_id in [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-5",
        "gpt-5.6-sol",
        "o1",
        "o3-mini",
        "claude-3-opus-20240229",
        "claude-3-5-sonnet-20241022",
        "claude-sonnet-4-20250514",
        "claude-opus-5-20260724",
        "claude-fable-5",
        "gemini-1.5-pro",
        "gemini-2.0-flash",
        "gemini-3-pro",
        "pixtral-12b-2409",
        "qwen2.5-vl-72b-instruct",
        "llama-4-scout",
        "gemma-3-27b-it",
        "deepseek-vl2",
    ]:
        assert model_supports_vision(model_id), model_id


def test_known_text_only_models_are_not_vision() -> None:
    for model_id in [
        "gpt-3.5-turbo",
        "claude-2.1",
        "claude-instant-1.2",
        "deepseek-chat",
        "deepseek-reasoner",
        "mistral-large-latest",
        "llama-3.1-70b",
        "gemma-2-27b",
        "qwen2.5-72b-instruct",
    ]:
        assert not model_supports_vision(model_id), model_id


def test_unknown_model_defaults_false() -> None:
    assert model_supports_vision("some-future-self-hosted-model") is False
    assert model_supports_vision("") is False
    assert model_supports_vision(None) is False


def test_resolve_model_id_and_agent_supports_vision_without_profile(tmp_path) -> None:
    store.rebind(tmp_path / "vision_no_profile.db")
    # No LLM profile configured — resolves to empty model id, never raises.
    assert resolve_model_id("nonexistent-agent") == ""
    assert agent_supports_vision("nonexistent-agent") is False


def test_agent_supports_vision_resolves_configured_profile(tmp_path) -> None:
    store.rebind(tmp_path / "vision_profile.db")
    store.create_llm_profile(
        {
            "id": "vis1",
            "name": "Vision test",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model": "gpt-4o-mini",
        }
    )
    store.set_default_llm_profile("vis1")
    assert resolve_model_id(None) == "gpt-4o-mini"
    assert agent_supports_vision(None) is True

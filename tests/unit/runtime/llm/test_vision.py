"""Vision (image-input) capability detection."""

from __future__ import annotations

import pytest

from app.runtime.llm import models_dev
from app.runtime.llm.vision import (
    agent_supports_vision,
    decide_image_input_mode,
    lookup_vision_capability,
    model_supports_vision,
    normalize_model_id,
    resolve_model_id,
    resolve_vision_profile,
    reset_vision_caches,
)
from app.services import store


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    """No real network in capability lookups: catalog reads as empty."""
    monkeypatch.setattr(models_dev, "_load_index", lambda **kw: {})
    reset_vision_caches()
    yield
    reset_vision_caches()


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


def test_vendor_prefixed_model_ids_match() -> None:
    """Aggregator-style ``vendor/model`` ids normalize before matching."""
    assert normalize_model_id("openai/gpt-4o") == "gpt-4o"
    assert model_supports_vision("openai/gpt-4o") is True
    assert model_supports_vision("anthropic/claude-sonnet-4-20250514") is True
    assert model_supports_vision("google/gemini-2.0-flash") is True
    assert model_supports_vision("meta-llama/llama-3.1-70b-instruct") is False


def test_capability_override_wins(tmp_path) -> None:
    store.rebind(tmp_path / "vision_override.db")
    store.update_settings(
        {"model_capability_overrides": {"deepseek-chat": {"supports_vision": True}}}
    )
    assert lookup_vision_capability("deepseek-chat") is True
    assert model_supports_vision("deepseek-chat") is True
    # And a false pin beats the prefix table.
    store.update_settings(
        {"model_capability_overrides": {"gpt-4o": {"supports_vision": False}}}
    )
    assert lookup_vision_capability("gpt-4o") is False
    assert model_supports_vision("gpt-4o") is False


def test_lookup_tri_state_unknown_is_none() -> None:
    # No match in any probe → None (unknown), not False.
    assert lookup_vision_capability("totally-unknown-local-model") is None
    assert lookup_vision_capability("") is None


def test_models_dev_catalog_verdict(monkeypatch) -> None:
    monkeypatch.setattr(
        models_dev,
        "_load_index",
        lambda **kw: {"openai": {"gpt-future": True, "gpt-text": False}},
    )
    assert lookup_vision_capability(
        "gpt-future", "https://api.openai.com/v1"
    ) is True
    assert lookup_vision_capability(
        "gpt-text", "https://api.openai.com/v1"
    ) is False
    # Aggregator ids: provider table keyed by vendor/model.
    monkeypatch.setattr(
        models_dev,
        "_load_index",
        lambda **kw: {"openrouter": {"acme/vision-x": True}},
    )
    assert lookup_vision_capability(
        "acme/vision-x", "https://openrouter.ai/api/v1"
    ) is True


def test_ollama_probe_reports_vision(monkeypatch) -> None:
    import httpx2

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"capabilities": ["completion", "vision"]}

    monkeypatch.setattr(httpx2, "post", lambda *a, **kw: _Resp())
    assert (
        lookup_vision_capability("llama3.2-vision", "http://localhost:11434/v1")
        is True
    )

    class _NoVision(_Resp):
        @staticmethod
        def json():
            return {"capabilities": ["completion"]}

    monkeypatch.setattr(httpx2, "post", lambda *a, **kw: _NoVision())
    assert (
        lookup_vision_capability("llama3.1", "http://localhost:11434/v1") is False
    )


def test_ollama_probe_not_attempted_for_remote(monkeypatch) -> None:
    import httpx2

    def _boom(*a, **kw):
        raise AssertionError("remote endpoints must not be probed")

    monkeypatch.setattr(httpx2, "post", _boom)
    assert lookup_vision_capability("llama3.2-vision", "https://api.openai.com/v1") is not False


def _profile(profile_id: str, model: str, base_url: str = "https://api.openai.com/v1"):
    store.create_llm_profile(
        {
            "id": profile_id,
            "name": profile_id,
            "base_url": base_url,
            "api_key": "sk-test",
            "model": model,
        }
    )


def test_decide_image_input_mode(tmp_path) -> None:
    store.rebind(tmp_path / "vision_mode.db")
    _profile("main", "gpt-4o")
    store.set_default_llm_profile("main")
    # auto + vision-capable main → native
    assert decide_image_input_mode(None) == "native"
    # explicit pins are absolute
    store.update_settings({"image_input_mode": "text"})
    assert decide_image_input_mode(None) == "text"
    store.update_settings({"image_input_mode": "native"})
    assert decide_image_input_mode(None) == "native"
    # pinned vision profile forces describe path even with vision main
    store.update_settings({"image_input_mode": "auto", "vision_profile_id": "v1"})
    assert decide_image_input_mode(None) == "text"


def test_decide_image_input_mode_text_only_main(tmp_path) -> None:
    store.rebind(tmp_path / "vision_mode_text.db")
    _profile("main", "deepseek-chat")
    store.set_default_llm_profile("main")
    assert decide_image_input_mode(None) == "text"


def test_resolve_vision_profile_order(tmp_path) -> None:
    store.rebind(tmp_path / "vision_resolve.db")
    _profile("textmain", "deepseek-chat")           # created first, text-only
    _profile("auxvis", "gpt-4o")                     # vision-capable fallback
    store.set_default_llm_profile("textmain")
    # auto: main is text-only → first enabled vision-capable profile wins
    prof = resolve_vision_profile(None)
    assert prof and prof["id"] == "auxvis"
    # pin wins outright, even for a model the probes don't know
    store.update_settings({"model_capability_overrides": {}})
    _profile("pinvis", "totally-unknown-model")
    store.update_settings({"vision_profile_id": "pinvis"})
    prof = resolve_vision_profile(None)
    assert prof and prof["id"] == "pinvis"


def test_resolve_vision_profile_main_capable(tmp_path) -> None:
    store.rebind(tmp_path / "vision_resolve_main.db")
    _profile("main", "claude-sonnet-4-20250514", "https://api.anthropic.com")
    store.set_default_llm_profile("main")
    prof = resolve_vision_profile(None)
    assert prof and prof["id"] == "main"

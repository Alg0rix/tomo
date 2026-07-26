"""LLM settings: seed defaults, masking, keep-on-blank API key."""

from __future__ import annotations

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "llm-settings.db")


def test_seed_includes_llm_keys(tmp_path) -> None:
    _rebind(tmp_path)
    s = store.get_settings()
    assert s["llm_base_url"] == "https://api.openai.com/v1"
    assert s["llm_api_key"] == ""
    assert s["llm_model"] == "gpt-4o-mini"


def test_public_settings_masks_key(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({"llm_api_key": "sk-abcdefghijklmnop"})
    pub = store.get_public_settings()
    assert pub["llm_api_key_set"] is True
    assert pub["llm_api_key"] == "••••mnop"
    assert "sk-abcdef" not in pub["llm_api_key"]
    assert store.get_settings()["llm_api_key"] == "sk-abcdefghijklmnop"


def test_blank_put_keeps_existing_key(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({"llm_api_key": "sk-keep-me-secret99"})
    store.update_settings({"llm_api_key": "", "llm_model": "gpt-4o"})
    assert store.get_settings()["llm_api_key"] == "sk-keep-me-secret99"
    assert store.get_settings()["llm_model"] == "gpt-4o"
    assert store.get_settings()["default_model"] == "gpt-4o"


def test_blank_model_or_url_does_not_clear(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({
        "llm_base_url": "https://keep.example/v1",
        "llm_model": "keep-model",
        "llm_api_key": "sk-abc",
    })
    store.update_settings({"llm_base_url": "", "llm_model": "   ", "llm_api_key": ""})
    s = store.get_settings()
    assert s["llm_base_url"] == "https://keep.example/v1"
    assert s["llm_model"] == "keep-model"
    assert s["llm_api_key"] == "sk-abc"


def test_llm_model_syncs_default_model(tmp_path) -> None:
    _rebind(tmp_path)
    store.update_settings({"llm_model": "claude-3.5"})
    assert store.get_settings()["default_model"] == "claude-3.5"

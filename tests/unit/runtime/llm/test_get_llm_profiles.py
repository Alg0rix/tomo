"""``get_llm(agent_id)`` resolution — per-agent profile → default → first enabled.

Exercises the Alpha §2.2 runtime resolution against a real temp SQLite DB with
seeded agents and user-created profiles. No network: ``OpenAICompatClient`` is
built but never called.
"""

from __future__ import annotations

import pytest

from app.runtime.llm import OpenAICompatClient, get_llm
from app.runtime.llm.openai_compat import LLMConfigError
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "get-llm-profiles.db")


def _profile(pid: str, base: str, model: str) -> dict:
    return {"id": pid, "name": pid, "base_url": base, "api_key": "sk-" + pid, "model": model}


def test_get_llm_uses_agent_profile_when_assigned(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://d/v1", "md"))
    store.set_default_llm_profile("default")
    store.create_llm_profile(_profile("fast", "https://f/v1", "mf"))
    store.update_agent("main", {"model_id": "fast"})
    client = get_llm(agent_id="main")
    assert isinstance(client, OpenAICompatClient)
    assert client.endpoint == "https://f/v1/chat/completions"


def test_get_llm_falls_back_to_default_when_agent_model_empty(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://d/v1", "md"))
    store.set_default_llm_profile("default")
    # main is seeded with empty model_id -> default profile.
    client = get_llm(agent_id="main")
    assert client.endpoint == "https://d/v1/chat/completions"


def test_get_llm_falls_back_to_default_when_agent_profile_disabled(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://d/v1", "md"))
    store.set_default_llm_profile("default")
    store.create_llm_profile(_profile("off", "https://o/v1", "mo"))
    store.update_llm_profile("off", {"enabled": False})
    store.update_agent("main", {"model_id": "off"})
    client = get_llm(agent_id="main")
    assert client.endpoint == "https://d/v1/chat/completions"


def test_get_llm_uses_first_enabled_when_no_default(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("a", "https://a/v1", "ma"))
    client = get_llm()
    assert client.endpoint == "https://a/v1/chat/completions"


def test_get_llm_raises_when_no_profiles(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(LLMConfigError, match="System"):
        get_llm()


def test_get_llm_raises_when_profile_has_no_api_key(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "p", "name": "P", "base_url": "", "api_key": "", "model": "m"}
    )
    store.set_default_llm_profile("p")
    with pytest.raises(LLMConfigError, match="System"):
        get_llm()


def test_two_agents_two_profiles_use_different_endpoints(tmp_path) -> None:
    """Two profiles + two agents on different profiles -> right endpoint each."""
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://d/v1", "md"))
    store.set_default_llm_profile("default")
    store.create_llm_profile(_profile("ops_prof", "https://ops/v1", "mops"))
    store.update_agent("ops", {"model_id": "ops_prof"})
    assert get_llm(agent_id="main").endpoint == "https://d/v1/chat/completions"
    assert get_llm(agent_id="ops").endpoint == "https://ops/v1/chat/completions"


def test_get_llm_returns_codex_client_for_subscription_profile(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_responses import CodexResponsesClient

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=99999999999.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    client = get_llm()
    assert isinstance(client, CodexResponsesClient)


def test_get_llm_raises_needs_reauth_message(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_oauth import CodexAuthError

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(*a, **kw):
        raise CodexAuthError("expired", code="invalid_grant", relogin_required=True)

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh)
    with pytest.raises(LLMConfigError, match="ChatGPT sign-in expired"):
        get_llm()


def test_get_llm_threads_reasoning_effort_into_codex_client(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=99999999999.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    store.update_llm_profile(created["id"], {"reasoning_efforts": ["low", "high"]})
    client = get_llm()
    assert client._reasoning_effort == "high"

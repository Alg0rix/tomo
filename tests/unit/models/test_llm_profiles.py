"""LLM model profiles: CRUD, encrypted api_key, masked public view, default.

Covers the Slice A §2.2 invariants:
* ``api_key`` is always **ciphertext** at rest (``enc:v1:`` prefix) — never
  plaintext in the raw DB column.
* Public views (list / get / create / update returns) **mask** the key and add
  ``api_key_set``; the decrypted key never crosses an API/HTML boundary.
* A **blank** ``api_key`` on update keeps the existing ciphertext (never clears).
* ``default_model_id`` setting points at a profile id; resolve falls back
  agent → default → first enabled.
"""

from __future__ import annotations

import time

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "llm-profiles.db")


def _raw_api_key(profile_id: str) -> str:
    """Read the raw (ciphertext) api_key column from the store's own connection."""
    row = store._conn.execute(
        "SELECT api_key FROM llm_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    return row["api_key"] if row else ""


def test_create_profile_encrypts_api_key(tmp_path) -> None:
    _rebind(tmp_path)
    prof = store.create_llm_profile(
        {
            "id": "default",
            "name": "Default",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-1234567890abcdef",
            "model": "gpt-4o-mini",
        }
    )
    assert prof["id"] == "default"
    assert prof["api_key_set"] is True
    # Public view masks the key — never the raw secret.
    assert prof["api_key"] == "••••cdef"
    assert "sk-test" not in prof["api_key"]
    # Raw DB column is ciphertext, not plaintext.
    raw = _raw_api_key("default")
    assert raw.startswith("enc:v1:")
    assert "sk-test" not in raw


def test_list_profiles_returns_masked(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "a", "name": "A", "base_url": "", "api_key": "sk-aaaaaaaa1234", "model": "m"}
    )
    store.create_llm_profile(
        {"id": "b", "name": "B", "base_url": "", "api_key": "", "model": "m"}
    )
    by_id = {p["id"]: p for p in store.list_llm_profiles()}
    assert by_id["a"]["api_key_set"] is True
    assert by_id["a"]["api_key"] == "••••1234"
    assert by_id["b"]["api_key_set"] is False
    assert by_id["b"]["api_key"] == ""


def test_blank_put_keeps_existing_key(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "p", "name": "P", "base_url": "https://x/v1", "api_key": "sk-keepsecret99", "model": "m1"}
    )
    raw_before = _raw_api_key("p")
    updated = store.update_llm_profile("p", {"api_key": "", "model": "m2"})
    assert updated["model"] == "m2"
    assert updated["api_key_set"] is True
    # Ciphertext unchanged — blank key never clears.
    assert _raw_api_key("p") == raw_before


def test_update_with_new_key_reencrypts(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {"id": "p", "name": "P", "base_url": "", "api_key": "sk-old", "model": "m"}
    )
    store.update_llm_profile("p", {"api_key": "sk-new1234"})
    raw = _raw_api_key("p")
    assert raw.startswith("enc:v1:")
    # Runtime resolution decrypts to the new key.
    assert store.resolve_llm_profile(None)["api_key"] == "sk-new1234"


def test_set_and_get_default(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "a", "name": "A", "base_url": "", "api_key": "sk-a", "model": "m"})
    store.create_llm_profile({"id": "b", "name": "B", "base_url": "", "api_key": "sk-b", "model": "m"})
    assert store.get_default_llm_profile_id() == ""
    store.set_default_llm_profile("b")
    assert store.get_default_llm_profile_id() == "b"


def test_delete_profile(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "base_url": "", "api_key": "sk", "model": "m"})
    assert store.delete_llm_profile("p") is True
    assert store.delete_llm_profile("p") is False
    assert store.list_llm_profiles() == []


def test_create_duplicate_profile_raises(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "base_url": "", "api_key": "sk", "model": "m"})
    import pytest

    with pytest.raises(ValueError):
        store.create_llm_profile({"id": "p", "name": "Dup", "base_url": "", "api_key": "sk", "model": "m"})


def test_resolve_profile_agent_then_default_then_first(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "default", "name": "D", "base_url": "https://d/v1", "api_key": "sk-d", "model": "md"})
    store.set_default_llm_profile("default")
    store.create_llm_profile({"id": "fast", "name": "F", "base_url": "https://f/v1", "api_key": "sk-f", "model": "mf"})
    # Agent "main" (seeded, empty model_id) -> default profile.
    assert store.resolve_llm_profile("main")["id"] == "default"
    # Assign fast to main -> agent profile wins.
    store.update_agent("main", {"model_id": "fast"})
    assert store.resolve_llm_profile("main")["id"] == "fast"
    # No agent id -> default.
    assert store.resolve_llm_profile(None)["id"] == "default"


def test_resolve_profile_returns_none_when_empty(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.resolve_llm_profile(None) is None
    assert store.resolve_llm_profile("main") is None


def test_profile_reasoning_efforts_are_normalized_and_default_to_last(tmp_path) -> None:
    _rebind(tmp_path)
    profile = store.create_llm_profile(
        {
            "id": "p",
            "name": "P",
            "api_key": "sk-p",
            "model": "model-a",
            "reasoning_efforts": [" low ", "", "high", "low"],
        }
    )

    assert profile["reasoning_efforts"] == ["low", "high"]
    assert store.resolve_llm_profile(None)["reasoning_efforts"] == ["low", "high"]


def test_session_reasoning_effort_persists_and_falls_back_when_profile_changes(
    tmp_path,
) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            "id": "default",
            "name": "D",
            "api_key": "sk-d",
            "model": "model-a",
            "reasoning_efforts": ["low", "high"],
        }
    )
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"], user_id="web")

    state = store.get_session_reasoning_effort(sid)
    assert state["reasoning_efforts"] == ["low", "high"]
    assert state["reasoning_effort"] == "high"

    store.set_session_reasoning_effort(sid, "low")
    assert store.get_session(sid)["reasoning_effort"] == "low"
    assert store.resolve_session_reasoning_effort(sid, "main") == "low"

    store.update_llm_profile("default", {"reasoning_efforts": ["minimal", "max"]})
    assert store.resolve_session_reasoning_effort(sid, "main") == "max"

    store.create_llm_profile(
        {
            "id": "ops_profile",
            "name": "Ops",
            "api_key": "sk-ops",
            "model": "ops-model",
            "reasoning_efforts": ["quick-ops", "max-ops"],
        }
    )
    store.update_agent("ops", {"model_id": "ops_profile"})
    assert store.resolve_session_reasoning_effort(sid, "ops") == "max-ops"


def test_session_reasoning_effort_rejects_unknown_value(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            "id": "default",
            "name": "D",
            "api_key": "sk-d",
            "model": "model-a",
            "reasoning_efforts": ["low", "high"],
        }
    )
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"])

    import pytest

    with pytest.raises(ValueError, match="reasoning effort"):
        store.set_session_reasoning_effort(sid, "unsupported")


def test_create_subscription_profile_encrypts_tokens(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        prof = llm_profiles_store.create_subscription_profile(
            store._conn,
            provider="openai-codex",
            access_token="at-secret-123",
            refresh_token="rt-secret-456",
            expires_at=9999999999.0,
            name="ChatGPT (Codex)",
            model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
    assert prof["auth_mode"] == "subscription"
    assert prof["subscription_provider"] == "openai-codex"
    assert prof["access_token_set"] is True
    assert prof["refresh_token_set"] is True
    assert "access_token" not in prof
    assert "refresh_token" not in prof
    raw = store._conn.execute(
        "SELECT access_token, refresh_token FROM llm_profiles WHERE id=?", (prof["id"],)
    ).fetchone()
    assert raw["access_token"].startswith("enc:v1:")
    assert "at-secret-123" not in raw["access_token"]
    assert raw["refresh_token"].startswith("enc:v1:")


def test_find_subscription_profile_returns_decrypted(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=123.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        found = llm_profiles_store.find_subscription_profile(store._conn, "openai-codex")
    assert found is not None
    assert found["id"] == created["id"]
    assert found["access_token"] == "at-1"
    assert found["refresh_token"] == "rt-1"
    assert found["token_expires_at"] == 123.0


def test_save_subscription_tokens_reencrypts(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-old",
            refresh_token="rt-old", expires_at=1.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.save_subscription_tokens(
            store._conn, created["id"],
            access_token="at-new", refresh_token="rt-new", expires_at=2.0,
        )
        refreshed = llm_profiles_store.get_profile(store._conn, created["id"])
    assert refreshed["access_token"] == "at-new"
    assert refreshed["refresh_token"] == "rt-new"
    assert refreshed["token_expires_at"] == 2.0


def test_resolve_profile_includes_subscription_fields(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-x",
            refresh_token="rt-x", expires_at=42.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    resolved = store.resolve_llm_profile(None)
    assert resolved["auth_mode"] == "subscription"
    assert resolved["access_token"] == "at-x"
    assert resolved["token_expires_at"] == 42.0


def test_resolve_profile_refreshes_expiring_subscription_token(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,  # already expired
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(refresh_token, **kw):
        assert refresh_token == "rt-1"
        return {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_at": 99999999999.0}

    monkeypatch.setattr(
        "app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh
    )
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False
    raw = store._conn.execute(
        "SELECT access_token FROM llm_profiles WHERE id=?", (created["id"],)
    ).fetchone()
    assert "at-fresh" not in raw["access_token"]  # persisted encrypted, not plaintext
    assert raw["access_token"].startswith("enc:v1:")


def test_resolve_profile_skips_refresh_when_token_fresh(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-fresh",
            refresh_token="rt-1", expires_at=time.time() + 3600,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fail_refresh(*a, **kw):
        raise AssertionError("refresh should not be called for a fresh token")

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fail_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False


def test_resolve_profile_flags_needs_reauth_on_terminal_refresh_failure(tmp_path, monkeypatch) -> None:
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

    def fake_refresh(refresh_token, **kw):
        raise CodexAuthError("expired", code="invalid_grant", relogin_required=True)

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is True


def test_resolve_profile_api_key_profile_has_needs_reauth_false(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "api_key": "sk-p", "model": "m"})
    store.set_default_llm_profile("p")
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is False

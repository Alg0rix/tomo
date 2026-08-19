"""API + page-render sanity for LLM profiles and agent role (Slice A).

Hits the real FastAPI app with the auth dependency stubbed so the profile CRUD
routes, the agent role round-trip, and the server-rendered System/Agents pages
are verified end to end (the UI JS is hard to unit-test).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "profiles-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.pop(require_auth, None)


def test_profile_crud_via_api(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/llm-profiles",
            json={
                "id": "default",
                "name": "Default",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-secret1234",
                "model": "gpt-4o-mini",
                "reasoning_efforts": ["low", "provider-max"],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["api_key_set"] is True
        assert body["api_key"] == "••••1234"  # masked, never raw
        assert body["reasoning_efforts"] == ["low", "provider-max"]
        assert "sk-secret" not in res.text

        res = client.get("/api/llm-profiles")
        assert res.status_code == 200
        data = res.json()
        assert len(data["profiles"]) == 1
        assert data["default_id"] == ""

        res = client.post("/api/llm-profiles/default/default")
        assert res.status_code == 200
        assert res.json()["default_id"] == "default"
        assert store.get_default_llm_profile_id() == "default"

        # Blank key on PUT keeps the existing ciphertext.
        res = client.put("/api/llm-profiles/default", json={"model": "gpt-4o", "api_key": ""})
        assert res.status_code == 200
        assert res.json()["model"] == "gpt-4o"
        assert res.json()["api_key_set"] is True
        assert store.resolve_llm_profile(None)["api_key"] == "sk-secret1234"

        res = client.delete("/api/llm-profiles/default")
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert client.get("/api/llm-profiles").json()["profiles"] == []
    finally:
        _cleanup()


def test_agent_role_via_api(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.put("/api/agents/main", json={"role": "lead coordinator", "model_id": ""})
        assert res.status_code == 200
        assert res.json()["role"] == "lead coordinator"
        res = client.get("/api/agents/main")
        assert res.status_code == 200
        assert res.json()["role"] == "lead coordinator"
    finally:
        _cleanup()


def test_pages_render_with_profiles(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        store.create_llm_profile(
            {
                "id": "default",
                "name": "Default",
                "base_url": "https://x/v1",
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
            }
        )
        store.set_default_llm_profile("default")
        for path in ("/system", "/agents", "/agents/main"):
            res = client.get(path)
            assert res.status_code == 200, f"{path} -> {res.status_code}"
        assert "profReasoningEfforts" in client.get("/system").text
        assert "composer-reasoning-trigger" in client.get("/sessions").text
    finally:
        _cleanup()


def test_session_reasoning_effort_api_round_trip(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        store.create_llm_profile(
            {
                "id": "default",
                "name": "D",
                "api_key": "sk-d",
                "model": "model-a",
                "reasoning_efforts": ["balanced", "deep"],
            }
        )
        store.set_default_llm_profile("default")
        sid = store.create_swarm_session(["main"], user_id="web")

        res = client.get(f"/api/sessions/{sid}/reasoning-effort")
        assert res.status_code == 200
        assert res.json()["reasoning_effort"] == "deep"

        res = client.put(
            f"/api/sessions/{sid}/reasoning-effort",
            json={"reasoning_effort": "balanced"},
        )
        assert res.status_code == 200
        assert res.json()["reasoning_effort"] == "balanced"
        assert store.get_session(sid)["reasoning_effort"] == "balanced"

        bad = client.put(
            f"/api/sessions/{sid}/reasoning-effort",
            json={"reasoning_effort": "not-supported"},
        )
        assert bad.status_code == 400

        reset = client.put(
            f"/api/sessions/{sid}/reasoning-effort",
            json={"reasoning_effort": ""},
        )
        assert reset.status_code == 200
        assert reset.json()["reasoning_effort"] == "deep"
    finally:
        _cleanup()


def test_setup_creates_default_profile(tmp_path) -> None:
    client = _client(tmp_path)
    # Force setup incomplete so the endpoint accepts the call.
    store.update_settings({"setup_complete": False})
    try:
        res = client.post(
            "/api/setup",
            json={
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-setup-key",
                "model": "gpt-4o-mini",
            },
        )
        assert res.status_code == 200
        assert store.get_default_llm_profile_id() == "default"
        prof = store.resolve_llm_profile(None)
        assert prof is not None
        assert prof["model"] == "gpt-4o-mini"
        assert prof["api_key"] == "sk-setup-key"
        assert store.is_setup_complete() is True
    finally:
        _cleanup()


def test_agent_generate_via_api(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        async def _fake(brief, *, llm=None, existing_agents=None):
            return {
                "name": "NetOps",
                "role": "ops",
                "description": "Network operations specialist.",
                "suggested_id": "netops",
                "system_prompt": "# NetOps\n\n## Responsibilities\n- Monitor infra",
            }

        monkeypatch.setattr(
            "app.runtime.agent_generate.generate_agent_draft",
            _fake,
        )

        res = client.post("/api/agents/generate", json={"brief": "network ops"})
        assert res.status_code == 200
        body = res.json()
        assert body["name"] == "NetOps"
        assert body["suggested_id"] == "netops"
        assert "Responsibilities" in body["system_prompt"]
    finally:
        _cleanup()


def test_codex_login_start_returns_device_code(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        def fake_start(**kw):
            return {
                "user_code": "ABCD-1234", "device_auth_id": "dev-1",
                "verification_url": "https://auth.openai.com/codex/device", "interval": 5,
            }
        monkeypatch.setattr("app.api.platform.codex_oauth.start_device_login", fake_start)
        res = client.post("/api/llm-profiles/codex-login/start")
        assert res.status_code == 200
        body = res.json()
        assert body["user_code"] == "ABCD-1234"
        assert body["device_auth_id"] == "dev-1"
    finally:
        _cleanup()


def test_codex_login_poll_pending(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", lambda *a, **kw: None)
        res = client.post(
            "/api/llm-profiles/codex-login/poll",
            json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "pending"}
    finally:
        _cleanup()


def test_codex_login_poll_success_creates_profile(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        def fake_poll(device_auth_id, user_code, **kw):
            return {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": 99999999999.0}
        monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", fake_poll)
        res = client.post(
            "/api/llm-profiles/codex-login/poll",
            json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["profile"]["auth_mode"] == "subscription"
        assert body["profile"]["access_token_set"] is True
        assert "access_token" not in body["profile"]
        assert "at-1" not in res.text

        # Second login updates the same profile rather than creating a duplicate.
        res2 = client.post(
            "/api/llm-profiles/codex-login/poll",
            json={"device_auth_id": "dev-2", "user_code": "EFGH-5678"},
        )
        assert res2.json()["profile"]["id"] == body["profile"]["id"]
    finally:
        _cleanup()


def test_codex_login_poll_terminal_failure_returns_400(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        from app.runtime.llm.codex_oauth import CodexAuthError

        def fake_poll(*a, **kw):
            raise CodexAuthError("boom", code="device_code_poll_error")
        monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", fake_poll)
        res = client.post(
            "/api/llm-profiles/codex-login/poll",
            json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
        )
        assert res.status_code == 400
    finally:
        _cleanup()


def test_codex_models_without_profile_id_returns_curated_defaults(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        from app.runtime.llm.codex_models import DEFAULT_CODEX_MODELS

        res = client.get("/api/llm-profiles/codex-models")
        assert res.status_code == 200
        assert res.json()["models"] == DEFAULT_CODEX_MODELS
    finally:
        _cleanup()


def test_codex_models_with_profile_id_uses_stored_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:
        from app.models.mixins import llm_profiles as llm_profiles_store

        with store._lock:
            created = llm_profiles_store.create_subscription_profile(
                store._conn, provider="openai-codex", access_token="at-1",
                refresh_token="rt-1", expires_at=99999999999.0,
                name="ChatGPT (Codex)", model="gpt-5-codex",
                base_url="https://chatgpt.com/backend-api/codex",
            )

        captured = {}

        def fake_list(access_token, **kw):
            captured["access_token"] = access_token
            return ["gpt-5.6-sol"]

        monkeypatch.setattr("app.api.platform.codex_models.list_codex_models", fake_list)
        res = client.get(f"/api/llm-profiles/codex-models?profile_id={created['id']}")
        assert res.status_code == 200
        assert res.json()["models"] == ["gpt-5.6-sol"]
        assert captured["access_token"] == "at-1"
    finally:
        _cleanup()

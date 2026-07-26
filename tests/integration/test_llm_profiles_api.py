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
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["api_key_set"] is True
        assert body["api_key"] == "••••1234"  # masked, never raw
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

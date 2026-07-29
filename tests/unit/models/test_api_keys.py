"""API keys — create, authenticate, revoke, Bearer access."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def test_create_and_authenticate_api_key(tmp_path) -> None:
    store.rebind(tmp_path / "apikey.db")
    admin = store.get_user_by_username("admin")
    created = store.create_api_key(admin["id"], "test")
    assert created["token"].startswith("tomo_")
    assert "key_hash" not in created
    assert store.authenticate_api_key(created["token"])["user_id"] == admin["id"]
    assert store.authenticate_api_key("tomo_bogus") is None
    assert store.authenticate_api_key("not-a-key") is None


def test_revoke_api_key(tmp_path) -> None:
    store.rebind(tmp_path / "apikey-revoke.db")
    admin = store.get_user_by_username("admin")
    created = store.create_api_key(admin["id"], "temp")
    token = created["token"]
    assert store.delete_api_key(created["id"]) is True
    assert store.authenticate_api_key(token) is None


def test_cascade_delete_with_user(tmp_path) -> None:
    store.rebind(tmp_path / "apikey-cascade.db")
    store.create_user({"username": "bob", "password": "pass1"})
    bob = store.get_user_by_username("bob")
    created = store.create_api_key(bob["id"], "bob-key")
    token = created["token"]
    store.delete_user(bob["id"])
    assert store.authenticate_api_key(token) is None


def test_bearer_auth_on_api(tmp_path) -> None:
    store.rebind(tmp_path / "apikey-http.db")
    # No dependency override — real auth via API key.
    client = TestClient(app)
    admin = store.get_user_by_username("admin")
    token = store.create_api_key(admin["id"], "http")["token"]

    res = client.get("/api/users")
    assert res.status_code == 401

    res = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert any(u["username"] == "admin" for u in res.json()["users"])

    res = client.get("/api/users", headers={"X-API-Key": token})
    assert res.status_code == 200

    res = client.get("/api/users", headers={"Authorization": "Bearer tomo_invalid"})
    assert res.status_code == 401


def test_api_keys_crud_via_session_override(tmp_path) -> None:
    store.rebind(tmp_path / "apikey-crud.db")
    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        admin = store.get_user_by_username("admin")
        res = client.post(
            "/api/api-keys",
            json={"user_id": admin["id"], "name": "ci"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["token"].startswith("tomo_")
        kid = body["id"]

        res = client.get("/api/api-keys")
        assert res.status_code == 200
        assert any(k["id"] == kid for k in res.json()["keys"])
        assert all("token" not in k for k in res.json()["keys"])

        res = client.delete(f"/api/api-keys/{kid}")
        assert res.status_code == 200
        assert store.get_api_key(kid) is None
    finally:
        app.dependency_overrides.pop(require_auth, None)

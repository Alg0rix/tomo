"""Login accounts — passwords, bootstrap, CRUD guards, login POST."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.core.passwords import hash_password, verify_password
from app.main import app
from app.services import store


def test_scrypt_roundtrip() -> None:
    stored = hash_password("secret")
    assert stored.startswith("scrypt$")
    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)


def test_bootstrap_admin(tmp_path) -> None:
    store.rebind(tmp_path / "users-bootstrap.db")
    users = store.list_users()
    assert len(users) == 1
    assert users[0]["username"] == "admin"
    assert users[0]["enabled"] is True
    assert "password_hash" not in users[0]
    assert store.authenticate("admin", "tomo") is not None
    assert store.authenticate("admin", "nope") is None
    assert store.authenticate("ADMIN", "tomo") is not None  # case-insensitive


def test_create_and_auth_second_user(tmp_path) -> None:
    store.rebind(tmp_path / "users-create.db")
    u = store.create_user(
        {"username": "alice", "password": "pass1", "display_name": "Alice"}
    )
    assert u["username"] == "alice"
    assert store.authenticate("alice", "pass1")["id"] == u["id"]
    assert store.authenticate("alice", "wrong") is None


def test_cannot_delete_last_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-last.db")
    admin = store.list_users()[0]
    try:
        store.delete_user(admin["id"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "last enabled" in str(e).lower()


def test_cannot_disable_last_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-disable.db")
    admin = store.list_users()[0]
    try:
        store.update_user(admin["id"], {"enabled": False})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "last enabled" in str(e).lower()


def test_delete_ok_when_another_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-delete.db")
    store.create_user({"username": "bob", "password": "pass1"})
    admin = store.get_user_by_username("admin")
    assert store.delete_user(admin["id"]) is True
    assert store.get_user_by_username("admin") is None


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "users-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.pop(require_auth, None)


def test_users_api_crud(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.get("/api/users")
        assert res.status_code == 200
        assert len(res.json()["users"]) == 1

        res = client.post(
            "/api/users",
            json={"username": "carol", "password": "pass1", "display_name": "Carol"},
        )
        assert res.status_code == 200
        uid = res.json()["id"]
        assert "password_hash" not in res.json()

        res = client.put(
            f"/api/users/{uid}",
            json={"display_name": "Carol K", "password": "pass2"},
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "Carol K"
        assert store.authenticate("carol", "pass2") is not None

        res = client.delete(f"/api/users/{uid}")
        assert res.status_code == 200
        assert store.get_user(uid) is None
    finally:
        _cleanup()


def test_api_rejects_delete_last_enabled(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        admin = store.get_user_by_username("admin")
        res = client.delete(f"/api/users/{admin['id']}")
        assert res.status_code == 400
        assert "last enabled" in res.json()["detail"].lower()
    finally:
        _cleanup()


def test_login_post_success_and_fail(tmp_path) -> None:
    store.rebind(tmp_path / "users-login.db")
    # Real session middleware — no auth override.
    client = TestClient(app)
    res = client.post(
        "/login",
        data={"username": "admin", "password": "wrong", "next": "/"},
    )
    assert res.status_code == 401

    res = client.post(
        "/login",
        data={"username": "admin", "password": "tomo", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    res = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "tomo",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    res = client.post(
        "/login",
        data={"username": "admin", "password": "tomo", "next": "/sessions"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/sessions"


def test_system_page_includes_accounts(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.get("/system")
        assert res.status_code == 200
        assert b"Accounts" in res.content
        assert b"sec-users" in res.content
        assert b"admin" in res.content
    finally:
        _cleanup()

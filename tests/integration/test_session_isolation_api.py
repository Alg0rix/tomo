"""API: multi-user sessions stay private to the owner account."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def _login(client: TestClient, username: str, password: str) -> None:
    res = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code in (303, 302), res.text


def test_users_cannot_see_or_open_each_others_sessions(tmp_path) -> None:
    store.rebind(tmp_path / "iso-api.db")
    app.dependency_overrides.pop(require_auth, None)

    alice = store.create_user(
        {"username": "alice", "password": "password1", "display_name": "Alice"}
    )
    bob = store.create_user(
        {"username": "bob", "password": "password1", "display_name": "Bob"}
    )
    # Bootstrap admin exists; use real login for cookie sessions.
    alice_sid = store.create_swarm_session(["main"], user_id=alice["id"])
    bob_sid = store.create_swarm_session(["main"], user_id=bob["id"])
    store.append_session_history(
        alice_sid, {"type": "user", "content": "alice secret", "ts": 1.0}
    )
    store.append_session_history(
        bob_sid, {"type": "user", "content": "bob secret", "ts": 1.0}
    )

    client = TestClient(app)
    try:
        _login(client, "alice", "password1")

        listed = client.get("/api/sessions")
        assert listed.status_code == 200
        ids = {s["id"] for s in listed.json()["sessions"]}
        assert alice_sid in ids
        assert bob_sid not in ids

        own = client.get(f"/api/sessions/{alice_sid}")
        assert own.status_code == 200
        assert own.json()["id"] == alice_sid

        other = client.get(f"/api/sessions/{bob_sid}")
        assert other.status_code == 404

        other_chat = client.get(f"/api/sessions/{bob_sid}/chat")
        assert other_chat.status_code == 404

        other_del = client.delete(f"/api/sessions/{bob_sid}")
        assert other_del.status_code == 404
        assert store.get_session(bob_sid) is not None

        # Creating a session attributes to the logged-in user, not body user_id.
        created = client.post(
            "/api/sessions",
            json={"agent_ids": ["main"], "user_id": bob["id"]},
        )
        assert created.status_code == 200
        new_sid = created.json()["session_id"]
        sess = store.get_session(new_sid)
        assert sess is not None
        assert sess["user_id"] == alice["id"]

        search = client.get("/api/sessions/search", params={"q": "bob secret"})
        assert search.status_code == 200
        hits = {r["session_id"] for r in search.json()["results"]}
        assert bob_sid not in hits
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_dashboard_recent_is_per_user(tmp_path) -> None:
    store.rebind(tmp_path / "iso-dash.db")
    app.dependency_overrides.pop(require_auth, None)

    alice = store.create_user(
        {"username": "alice2", "password": "password1", "display_name": "Alice"}
    )
    bob = store.create_user(
        {"username": "bob2", "password": "password1", "display_name": "Bob"}
    )
    alice_sid = store.create_swarm_session(["main"], user_id=alice["id"])
    store.create_swarm_session(["main"], user_id=bob["id"])

    client = TestClient(app)
    try:
        _login(client, "alice2", "password1")
        res = client.get("/api/dashboard/data")
        assert res.status_code == 200
        body = res.json()
        recent_ids = {s["id"] for s in body["recent_sessions"]}
        assert alice_sid in recent_ids
        assert body["stats"]["session_count"] == 1
        assert all(
            store.get_session(sid)["user_id"] == alice["id"] for sid in recent_ids
        )
    finally:
        app.dependency_overrides.pop(require_auth, None)

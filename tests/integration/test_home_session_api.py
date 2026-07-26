"""API: POST /api/sessions/home creates a coordinator-only session."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def test_post_sessions_home_coordinator_only(tmp_path) -> None:
    store.rebind(tmp_path / "home-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        res = client.post("/api/sessions/home", json={"message": "hi", "user_id": "web"})
        assert res.status_code == 200
        body = res.json()
        assert body["coordinator_id"] == "main"
        assert body["coordinator_name"] == "Tomo"
        assert body["message"] == "hi"
        assert body["session_id"]

        session = store.get_session(body["session_id"])
        assert session is not None
        assert session["agent_ids"] == ["main"]
        assert session["coordinator_id"] == "main"
    finally:
        app.dependency_overrides.pop(require_auth, None)

"""Companion REST endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def test_companion_snapshot_shape(tmp_path) -> None:
    store.rebind(tmp_path / "comp.db")
    store.update_settings({"setup_complete": True, "learning_enabled": True})
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        r = client.get("/api/companion")
        assert r.status_code == 200
        data = r.json()
        assert "bond" in data
        assert 0 <= data["bond"] <= 100
        assert "bond_parts" in data
        assert "stats" in data
        assert "growth" in data
        assert isinstance(data["growth"], list)
        assert "recent_events" in data
        assert "learning_enabled" in data
        assert "user_profile_preview" in data
        assert "heatmap" in data
        assert isinstance(data["heatmap"].get("days"), list)
        assert "streak" in data
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_companion_events_pagination(tmp_path) -> None:
    store.rebind(tmp_path / "comp2.db")
    app.dependency_overrides[require_auth] = lambda: None
    try:
        store.insert_learning_event(saved=True, diary="a", created_at=100.0)
        store.insert_learning_event(saved=False, note="idle", created_at=200.0)
        client = TestClient(app)
        r = client.get("/api/companion/events?limit=1")
        assert r.status_code == 200
        body = r.json()
        assert len(body["events"]) == 1
        assert body["events"][0]["created_at"] == 200.0
        r2 = client.get("/api/companion/events?limit=1&before=200")
        assert r2.status_code == 200
        assert r2.json()["events"][0]["created_at"] == 100.0
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_companion_page_renders(tmp_path) -> None:
    store.rebind(tmp_path / "comp3.db")
    store.update_settings({"setup_complete": True})
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        r = client.get("/companion")
        assert r.status_code == 200
        assert b"Companion" in r.content
        assert b"companion.js" in r.content
        assert b"companion.css" in r.content
    finally:
        app.dependency_overrides.pop(require_auth, None)

"""``/api/dashboard/prompts`` — dynamic Home 'Try asking' chip endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime import dashboard_prompts
from app.services import store


@pytest.fixture(autouse=True)
def _clean_cache():
    dashboard_prompts.clear_dashboard_prompts_cache()
    yield
    dashboard_prompts.clear_dashboard_prompts_cache()


def test_dashboard_prompts_requires_auth(tmp_path):
    store.rebind(tmp_path / "prompts_auth.db")
    client = TestClient(app)
    r = client.get("/api/dashboard/prompts")
    assert r.status_code == 401


def test_dashboard_prompts_shape_falls_back_when_unconfigured(tmp_path):
    # No LLM profile configured for this fresh DB → LLMConfigError → fallback.
    store.rebind(tmp_path / "prompts_fallback.db")
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        r = client.get("/api/dashboard/prompts")
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "fallback"
        assert len(data["prompts"]) == 3
        for p in data["prompts"]:
            assert set(p.keys()) == {"key", "label", "prompt"}
            assert p["key"] and p["label"] and p["prompt"]
    finally:
        app.dependency_overrides.pop(require_auth, None)

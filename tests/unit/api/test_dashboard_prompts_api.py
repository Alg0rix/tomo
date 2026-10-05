"""``/api/dashboard/prompts`` — dynamic Home 'Try asking' chip endpoint."""

from __future__ import annotations

import pytest

from tests.fakes.access import admin_client
from app.runtime import dashboard_prompts
from app.services import store


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    from app.core import config

    # Rebinding the DB does not clear Markdown vault files from previous tests.
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    dashboard_prompts.clear_dashboard_prompts_cache()
    yield
    dashboard_prompts.clear_dashboard_prompts_cache()


def test_dashboard_prompts_requires_auth(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app as _app

    store.rebind(tmp_path / "prompts_auth.db")
    client = TestClient(_app)
    r = client.get("/api/dashboard/prompts")
    assert r.status_code == 401


def test_dashboard_prompts_shape_falls_back_when_unconfigured(tmp_path):
    # No LLM profile configured for this fresh DB → LLMConfigError → fallback.
    # Fresh DB has no memory signals, so the generic pool is used.
    store.rebind(tmp_path / "prompts_fallback.db")
    client, _admin = admin_client()
    try:
        r = client.get("/api/dashboard/prompts")
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "fallback"
        assert len(data["prompts"]) == 3
        for p in data["prompts"]:
            assert set(p.keys()) == {"key", "label", "prompt"}
            assert p["key"] and p["label"] and p["prompt"]
    finally:
        client.close()


def test_dashboard_prompts_memory_fallback_when_unconfigured(tmp_path):
    # Same unconfigured-LLM path, but with a memory signal → fallback-memory.
    from app.services import store as _store

    store.rebind(tmp_path / "prompts_mem_fallback.db")
    client, _admin = admin_client()
    sid = _store.create_swarm_session(["main"], user_id=_admin["id"])
    _store.append_session_history(
        sid, {"type": "user", "content": "Help me debug the CCTV lane dashboard"}
    )
    dashboard_prompts.clear_dashboard_prompts_cache()
    try:
        r = client.get("/api/dashboard/prompts")
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "fallback-memory"
        assert len(data["prompts"]) == 3
        assert "cctv" in " ".join(p["prompt"] for p in data["prompts"]).lower()
    finally:
        client.close()

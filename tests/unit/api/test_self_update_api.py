from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "self-update.db")
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.pop(require_auth, None)


def test_update_api_disabled_outside_script_install(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.get("/api/update")
        assert res.status_code == 200
        body = res.json()
        assert body["can_update"] is False
        assert body["reason"] in {"container", "not_script_install"}
        assert body["kind"] in {"container", "dev"}
        assert "version" in body

        assert client.post("/api/update").status_code == 409
        assert client.post("/api/update/check").status_code == 409

        page = client.get("/system")
        assert page.status_code == 200
        assert 'id="selfUpdateField"' in page.text
        assert 'id="selfUpdateBtn"' in page.text
    finally:
        _cleanup()

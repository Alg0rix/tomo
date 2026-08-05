"""Integration tests for artifact public link sharing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime.artifacts.fs import write_artifact_text
from app.services import store


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "share-integration.db")
    # Disable auth so we can test both authenticated and public routes easily.
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def test_create_share_and_fetch_publicly(tmp_path):
    client = _client(tmp_path)
    try:
        # Need a session so the artifact endpoint sees it.
        sid = store.create_swarm_session(["main"], user_id="web")
        write_artifact_text(sid, "public.txt", "shared content")

        res = client.post(f"/api/sessions/{sid}/artifacts/public.txt/share")
        assert res.status_code == 200
        body = res.json()
        assert body["token"]
        assert body["share_url"].startswith("/share/")

        # Public raw endpoint works without auth.
        app.dependency_overrides.pop(require_auth, None)
        public_client = TestClient(app)
        raw = public_client.get(f"/api/share/{body['token']}/raw")
        assert raw.status_code == 200
        assert raw.text == "shared content"

        # Public viewer page renders.
        viewer = public_client.get(body["share_url"])
        assert viewer.status_code == 200
        assert b"shared content" in viewer.content or b"public.txt" in viewer.content

        # Authenticated delete revokes the share.
        app.dependency_overrides[require_auth] = lambda: None
        del_res = client.delete(f"/api/sessions/{sid}/artifacts/public.txt/share")
        assert del_res.status_code == 200

        public_client2 = TestClient(app)
        assert public_client2.get(f"/api/share/{body['token']}/raw").status_code == 404
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_share_idempotent(tmp_path):
    client = _client(tmp_path)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        write_artifact_text(sid, "stable.md", "x")

        first = client.post(f"/api/sessions/{sid}/artifacts/stable.md/share").json()
        second = client.post(f"/api/sessions/{sid}/artifacts/stable.md/share").json()
        assert first["token"] == second["token"]
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_get_share_status(tmp_path):
    client = _client(tmp_path)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        write_artifact_text(sid, "status.csv", "a,b\n")

        status = client.get(f"/api/sessions/{sid}/artifacts/status.csv/share")
        assert status.status_code == 200
        assert status.json()["shared"] is False

        created = client.post(f"/api/sessions/{sid}/artifacts/status.csv/share").json()
        status = client.get(f"/api/sessions/{sid}/artifacts/status.csv/share")
        assert status.json()["shared"] is True
        assert status.json()["token"] == created["token"]
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_public_routes_cannot_list_or_access_other_files(tmp_path):
    client = _client(tmp_path)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        write_artifact_text(sid, "visible.txt", "ok")
        write_artifact_text(sid, "secret.txt", "nope")
        token = client.post(f"/api/sessions/{sid}/artifacts/visible.txt/share").json()["token"]

        app.dependency_overrides.pop(require_auth, None)
        public_client = TestClient(app)

        # No listing via share token.
        assert public_client.get(f"/api/sessions/{sid}/artifacts").status_code == 401
        # Other file not accessible via token.
        assert public_client.get(f"/api/share/{token}/raw").text == "ok"
        assert public_client.get(f"/api/share/{token}/download").status_code == 200
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_missing_file_returns_404(tmp_path):
    client = _client(tmp_path)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        assert client.post(f"/api/sessions/{sid}/artifacts/missing.txt/share").status_code == 404
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_auth_viewer_has_share_button(tmp_path):
    client = _client(tmp_path)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        write_artifact_text(sid, "page.html", "<p>hi</p>")
        res = client.get(f"/sessions/{sid}/artifacts/page.html/view")
        assert res.status_code == 200
        assert b"id=\"avShareBtn\"" in res.content
    finally:
        app.dependency_overrides.pop(require_auth, None)

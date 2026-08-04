"""API integration tests for KB document upload (System -> Memory)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.services import store


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "knowledge-upload.db")
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.pop(require_auth, None)


def test_upload_txt_creates_entry(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("notes.txt", b"hello kb world", "text/plain")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "notes"
        assert body["body"] == "hello kb world"
        assert "uploaded" in body["tags"]
        assert "text" in body["tags"]
        assert body["upload"]["source_type"] == "text"
        assert body["upload"]["truncated"] is False
        assert body["upload"]["filename"] == "notes.txt"

        entries = client.get("/api/knowledge").json()["entries"]
        assert body["id"] in [e["id"] for e in entries]
    finally:
        _cleanup()


def test_upload_txt_with_title_and_tags_form(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/knowledge/upload",
            data={"title": "Manual Title", "tags": "alpha, beta, uploaded"},
            files={"file": ("notes.md", b"# Body", "text/markdown")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "Manual Title"
        for tag in ("alpha", "beta", "uploaded", "text"):
            assert tag in body["tags"]
        # deduped: "uploaded" only once
        assert body["tags"].count("uploaded") == 1
    finally:
        _cleanup()


def test_upload_title_capped(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        long_title = "T" * 250
        res = client.post(
            "/api/knowledge/upload",
            data={"title": long_title},
            files={"file": ("notes.txt", b"body", "text/plain")},
        )
        assert res.status_code == 200
        assert len(res.json()["title"]) == 200
    finally:
        _cleanup()


def test_upload_unsupported_ext_returns_400(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("evil.exe", b"MZ fake binary", "application/octet-stream")},
        )
        assert res.status_code == 400
        assert "unsupported" in res.json()["detail"]
    finally:
        _cleanup()


def test_upload_empty_file_returns_400(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert res.status_code == 400
    finally:
        _cleanup()


def test_upload_parser_non_valueerror_returns_400(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:

        def _boom(filename, data):
            raise RuntimeError("unexpected parser crash")

        monkeypatch.setattr("app.services.doc_parse.parse_document", _boom)
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert res.status_code == 400
        assert "failed to parse" in res.json()["detail"]
    finally:
        _cleanup()


def test_upload_parser_valueerror_returns_400(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    try:

        def _boom(filename, data):
            raise ValueError("No extractable text (PDF may be scanned/image-based)")

        monkeypatch.setattr("app.services.doc_parse.parse_document", _boom)
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("scan.pdf", b"%PDF", "application/pdf")},
        )
        assert res.status_code == 400
        assert "scanned" in res.json()["detail"]
    finally:
        _cleanup()


def test_upload_oversized_returns_400(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        # 20MB + 1 — stream should reject without needing full parse
        oversized = b"x" * (20 * 1024 * 1024 + 1)
        res = client.post(
            "/api/knowledge/upload",
            files={"file": ("big.txt", oversized, "text/plain")},
        )
        assert res.status_code == 400
        assert "too large" in res.json()["detail"]
    finally:
        _cleanup()


def test_existing_knowledge_crud_still_works(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.post(
            "/api/knowledge",
            json={"title": "manual", "body": "hand-written", "tags": ["manual"]},
        )
        assert res.status_code == 200
        eid = res.json()["id"]
        assert client.get(f"/api/knowledge/{eid}").status_code == 200
        assert client.put(f"/api/knowledge/{eid}", json={"body": "updated"}).status_code == 200
        assert client.delete(f"/api/knowledge/{eid}").status_code == 200
    finally:
        _cleanup()

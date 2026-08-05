"""Tests for artifact sharing DB primitives and store facade."""

from __future__ import annotations

import pytest

from app.runtime.artifacts.fs import write_artifact_text
from app.services import store


@pytest.fixture
def _fresh_store(tmp_path):
    store.rebind(tmp_path / "shares.db")


def test_share_artifact_creates_token(_fresh_store):
    write_artifact_text("sess_s1", "note.txt", "hello")
    share = store.share_artifact("sess_s1", "note.txt", created_by="web")
    assert share["session_id"] == "sess_s1"
    assert share["filename"] == "note.txt"
    assert share["created_by"] == "web"
    assert len(share["token"]) >= 24

    # Re-sharing returns the same token.
    again = store.share_artifact("sess_s1", "note.txt", created_by="other")
    assert again["token"] == share["token"]


def test_get_artifact_share_by_file(_fresh_store):
    write_artifact_text("sess_s2", "data.json", "{}")
    assert store.get_artifact_share_by_file("sess_s2", "data.json") is None

    created = store.share_artifact("sess_s2", "data.json")
    fetched = store.get_artifact_share_by_file("sess_s2", "data.json")
    assert fetched is not None
    assert fetched["token"] == created["token"]


def test_get_artifact_share_by_token(_fresh_store):
    write_artifact_text("sess_s3", "report.md", "# x")
    share = store.share_artifact("sess_s3", "report.md")

    fetched = store.get_artifact_share(share["token"])
    assert fetched is not None
    assert fetched["session_id"] == "sess_s3"
    assert fetched["filename"] == "report.md"

    assert store.get_artifact_share("not-a-token") is None
    assert store.get_artifact_share("") is None


def test_revoke_artifact_share(_fresh_store):
    write_artifact_text("sess_s4", "tmp.txt", "x")
    share = store.share_artifact("sess_s4", "tmp.txt")

    assert store.revoke_artifact_share("sess_s4", "tmp.txt") is True
    assert store.get_artifact_share(share["token"]) is None
    assert store.get_artifact_share_by_file("sess_s4", "tmp.txt") is None
    assert store.revoke_artifact_share("sess_s4", "tmp.txt") is False


def test_share_artifact_requires_existing_file_for_api_but_store_does_not_enforce(_fresh_store):
    # The store layer itself does not check filesystem existence; the API layer does.
    share = store.share_artifact("sess_missing", "ghost.txt")
    assert share["session_id"] == "sess_missing"

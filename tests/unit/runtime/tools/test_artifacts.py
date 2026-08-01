"""Tests for session-scoped filesystem artifacts + tools."""

from __future__ import annotations

import json
from pathlib import Path

import app.core.config as config
from app.core import home
from app.runtime.artifacts.fs import (
    bind_session,
    category_for,
    delete_artifact_file,
    list_artifact_files,
    reset_session,
    write_artifact_text,
)
from app.runtime.tools.registry import execute, reset_registry
from app.runtime.tools.sandbox import bind_agent, reset_agent
from app.services import store


def _rebind(tmp_path: Path, monkeypatch) -> None:
    reset_registry()
    store.rebind(tmp_path / "tomo.db")
    home_root = tmp_path / "home"
    work = tmp_path / "work"
    home_root.mkdir()
    work.mkdir()
    monkeypatch.setattr(config, "TOMO_HOME", home_root)
    monkeypatch.setattr(config, "TOMO_WORK", work)


def test_save_artifact_content_writes_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    sid = bind_session("sess_demo")
    try:
        out = execute(
            "save_artifact",
            {"filename": "report.md", "content": "# Hello\n"},
        )
        assert not out.startswith("Error"), out
        data = json.loads(out)
        assert data["filename"] == "report.md"
        assert data["session_id"] == "sess_demo"
        path = Path(data["filepath"])
        assert path.is_file()
        assert "sessions/sess_demo/artifacts" in str(path).replace("\\", "/")
        listed = list_artifact_files("sess_demo")
        assert listed["total"] == 1
    finally:
        reset_session(sid)
        reset_agent(token)


def test_artifacts_isolated_per_session(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    write_artifact_text("sess_a", "a.txt", "aaa")
    write_artifact_text("sess_b", "b.txt", "bbb")
    assert list_artifact_files("sess_a")["total"] == 1
    assert list_artifact_files("sess_a")["files"][0]["filename"] == "a.txt"
    assert list_artifact_files("sess_b")["files"][0]["filename"] == "b.txt"


def test_save_artifact_source_path_moves_local_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    sid = bind_session("sess_move")
    try:
        work = home.agent_work_dir("main")
        work.mkdir(parents=True, exist_ok=True)
        src = work / "out.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")
        out = execute(
            "save_artifact",
            {"filename": "pricing.csv", "source_path": "out.csv"},
        )
        assert not out.startswith("Error"), out
        data = json.loads(out)
        dest = Path(data["filepath"])
        assert dest.is_file()
        assert not src.exists()
    finally:
        reset_session(sid)
        reset_agent(token)


def test_list_and_fetch_artifact(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("coder")
    sid = bind_session("sess_fetch")
    try:
        write_artifact_text("sess_fetch", "notes.txt", "keep me")
        listed = execute("list_artifacts", {"filter": "notes"})
        data = json.loads(listed)
        assert data["total"] >= 1
        fetched = execute("fetch_artifact", {"filename": "notes.txt"})
        info = json.loads(fetched)
        assert info["filename"] == "notes.txt"
        assert info["session_id"] == "sess_fetch"
    finally:
        reset_session(sid)
        reset_agent(token)


def test_save_requires_session(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    token = bind_agent("main")
    try:
        out = execute(
            "save_artifact",
            {"filename": "x.md", "content": "no session"},
        )
        assert out.startswith("Error")
        assert "session" in out.lower()
    finally:
        reset_agent(token)


def test_legacy_catalog_save_artifact(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    out = execute(
        "save_artifact",
        {"title": "Report", "path": "/tmp/out.md", "kind": "report"},
    )
    assert out.startswith("Saved artifact")
    arts = store.search_artifacts("Report")
    assert arts and arts[0]["path"] == "/tmp/out.md"


def test_delete_artifact_file(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    write_artifact_text("sess_del", "gone.txt", "x")
    assert delete_artifact_file("sess_del", "gone.txt")
    assert list_artifact_files("sess_del")["total"] == 0


def test_artifacts_enabled_gates_tools(tmp_path: Path, monkeypatch) -> None:
    _rebind(tmp_path, monkeypatch)
    store.create_agent({"id": "arty", "name": "Arty", "artifacts_enabled": True})
    ids = store.get_enabled_tool_ids("arty")
    assert "save_artifact" in ids
    store.update_agent("arty", {"artifacts_enabled": False})
    ids2 = store.get_enabled_tool_ids("arty")
    assert "save_artifact" not in ids2


def test_category_for_html() -> None:
    assert category_for("dashboard.html") == "html"
    assert category_for("index.HTM") == "html"
    assert category_for("notes.md") == "markdown"
    assert category_for("data.csv") == "csv"
    assert category_for("payload.json") == "json"
    assert category_for("app.py") == "code"
    assert category_for("report.pdf") == "pdf"

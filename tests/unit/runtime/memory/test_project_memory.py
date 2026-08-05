"""Project memory lane (PROJECT.md)."""

from __future__ import annotations

from app.runtime.memory import project as project_mem


def test_project_add_and_near_duplicate(tmp_path) -> None:
    wid = "wp_demo"
    r1 = project_mem.add_entry(wid, "Uses FastAPI + SQLite", home_root=tmp_path)
    assert r1["ok"] is True
    assert "added" in (r1.get("message") or "").lower()

    r2 = project_mem.add_entry(wid, "Uses FastAPI + SQLite", home_root=tmp_path)
    assert r2["ok"] is True
    assert "already present" in (r2.get("message") or "").lower()

    snip = project_mem.format_snippet(wid, home_root=tmp_path)
    assert "FastAPI" in snip
    path = project_mem.project_path(wid, home_root=tmp_path)
    assert path is not None and path.exists()


def test_project_requires_workplace(tmp_path) -> None:
    r = project_mem.add_entry("", "note", home_root=tmp_path)
    assert r["ok"] is False

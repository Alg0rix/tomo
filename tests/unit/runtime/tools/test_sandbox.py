"""Sandbox path jail helpers."""

from __future__ import annotations

from pathlib import Path

from app.runtime.tools.sandbox import jail_path, resolve_work_root, bind_agent, reset_agent


def test_resolve_work_root_creates_dir() -> None:
    reset_agent()
    bind_agent("ops")
    root = resolve_work_root()
    assert root.is_dir()
    assert root.name == "work"
    assert "ops" in str(root)
    reset_agent()


def test_jail_allows_relative(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = jail_path(root, "a/b.txt")
    assert isinstance(target, Path)
    assert target == (root / "a" / "b.txt").resolve()


def test_jail_rejects_absolute(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    err = jail_path(root, "/etc/passwd")
    assert isinstance(err, str) and err.startswith("Error")


def test_jail_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    err = jail_path(root, "../secret")
    assert isinstance(err, str) and err.startswith("Error")

"""list_dir + jail_path absolute-under-root tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.runtime.tools.sandbox import jail_path


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_jail_path_absolute_under_root(tmp_path: Path) -> None:
    root = tmp_path / "wp"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    # Absolute path under root is allowed.
    abs_target = (root / "a.txt").resolve()
    got = jail_path(root, str(abs_target))
    assert got == abs_target


def test_jail_path_absolute_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "wp"
    root.mkdir()
    outside = tmp_path / "other" / "x.txt"
    outside.parent.mkdir()
    outside.write_text("y", encoding="utf-8")
    got = jail_path(root, str(outside.resolve()))
    assert isinstance(got, str) and got.startswith("Error")


def test_list_dir_lists_files() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("hi", encoding="utf-8")
    (work / "sub").mkdir(exist_ok=True)
    (work / "sub" / "nested.txt").write_text("n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute("list_dir", {"path": ".", "recursive": True, "max_depth": 2})
    assert "Workplace root:" in out
    assert "note.txt" in out
    assert "sub" in out

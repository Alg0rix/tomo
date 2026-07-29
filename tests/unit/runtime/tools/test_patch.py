"""patch tool (sandbox) tests."""

from __future__ import annotations

import pytest

from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_patch_happy_path() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "patch",
        {
            "path": "a.txt",
            "patch": "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n",
        },
    )
    assert "Applied" in result
    assert (work / "a.txt").read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_patch_create_file() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    target = work / "new.txt"
    if target.exists():
        target.unlink()
    sandbox.bind_agent("ops")
    result = execute(
        "patch",
        {
            "path": "new.txt",
            "patch": "@@ -0,0 +1,2 @@\n+hello\n+world\n",
        },
    )
    assert "Applied" in result
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"


def test_patch_missing_file() -> None:
    sandbox.bind_agent("ops")
    result = execute(
        "patch",
        {
            "path": "nope.txt",
            "patch": "@@ -1 +1 @@\n-x\n+y\n",
        },
    )
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_patch_escape_path() -> None:
    sandbox.bind_agent("ops")
    result = execute(
        "patch",
        {
            "path": "../x",
            "patch": "@@ -0,0 +1,1 @@\n+x\n",
        },
    )
    assert result.startswith("Error")


def test_patch_bad_hunks() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.txt").write_text("x\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute("patch", {"path": "a.txt", "patch": "not a patch"})
    assert result.startswith("Error")

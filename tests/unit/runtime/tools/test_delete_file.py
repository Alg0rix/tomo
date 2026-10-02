"""delete_file tool tests."""

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


def test_delete_file_happy_path() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    target = work / "gone.txt"
    target.write_text("bye", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute("delete_file", {"path": "gone.txt"})
    assert "Deleted" in result
    assert not target.exists()


def test_delete_file_missing_is_error() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute("delete_file", {"path": "nope.txt"})
    assert result.startswith("Error")


def test_delete_file_rejects_directory() -> None:
    work = home.agent_work_dir("ops")
    (work / "subdir").mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    result = execute("delete_file", {"path": "subdir"})
    assert result.startswith("Error")
    assert "directory" in result.lower()

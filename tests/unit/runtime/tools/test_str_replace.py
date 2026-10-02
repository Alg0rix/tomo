"""str_replace tool tests."""

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


def test_str_replace_happy_path() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("hello world", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "world", "new_string": "tomo"},
    )
    assert "Replaced" in result
    assert (work / "note.txt").read_text(encoding="utf-8") == "hello tomo"


def test_str_replace_missing_old_is_error() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("hello", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "missing", "new_string": "x"},
    )
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_str_replace_non_unique_is_error() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "aa", "new_string": "bb"},
    )
    assert result.startswith("Error")
    assert "unique" in result.lower() or "2" in result


def test_str_replace_count_all() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {
            "path": "note.txt",
            "old_string": "aa",
            "new_string": "bb",
            "count": -1,
        },
    )
    assert "Replaced" in result
    assert (work / "note.txt").read_text(encoding="utf-8") == "bb bb"


def test_str_replace_count_two() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {
            "path": "note.txt",
            "old_string": "aa",
            "new_string": "bb",
            "count": 2,
        },
    )
    assert "Replaced 2" in result
    assert (work / "note.txt").read_text(encoding="utf-8") == "bb bb"


def test_str_replace_escape_is_error() -> None:
    sandbox.bind_agent("ops")
    result = execute(
        "str_replace",
        {"path": "../x", "old_string": "a", "new_string": "b"},
    )
    assert result.startswith("Error")


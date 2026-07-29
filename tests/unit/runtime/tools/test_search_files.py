"""search_files tool tests."""

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


def test_search_files_finds_substring() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute("search_files", {"pattern": "hello"})
    assert "a.py:1:" in result
    assert "hello" in result


def test_search_files_glob_filters() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.py").write_text("needle\n", encoding="utf-8")
    (work / "b.txt").write_text("needle\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute("search_files", {"pattern": "needle", "glob": "*.py"})
    assert "a.py" in result
    assert "b.txt" not in result


def test_search_files_no_match() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.py").write_text("ok\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute("search_files", {"pattern": "zzz_missing"})
    assert result.startswith("No matches")


def test_search_files_bad_regex_is_error() -> None:
    sandbox.bind_agent("ops")
    result = execute("search_files", {"pattern": "[", "regex": True})
    assert result.startswith("Error")


def test_search_files_by_filename() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "app.py").write_text("x\n", encoding="utf-8")
    (work / "readme.md").write_text("y\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "search_files", {"pattern": "*.py", "target": "files"}
    )
    assert "app.py" in result
    assert "readme.md" not in result


def test_search_files_count_mode() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.txt").write_text("foo\nbar foo\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "search_files",
        {"pattern": "foo", "output_mode": "count"},
    )
    assert "a.txt" in result
    assert "2" in result


def test_search_files_context() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "c.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    result = execute(
        "search_files",
        {"pattern": "two", "context": 1},
    )
    assert "c.txt:1:" in result
    assert "c.txt:2:>" in result or "c.txt:2:" in result
    assert "three" in result

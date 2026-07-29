"""Upgraded read_file / write_file tests."""

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


def test_read_file_numbered_and_paginated() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "lines.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute("read_file", {"path": "lines.txt", "offset": 2, "limit": 2})
    assert "lines 2-3 of 4" in out
    assert "2|b" in out
    assert "3|c" in out
    assert "offset=4" in out or "after line 3" in out


def test_read_file_suggests_similar() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute("read_file", {"path": "config.yaaml"})
    assert out.startswith("Error")
    assert "Did you mean" in out
    assert "config.yaml" in out


def test_write_file_create_refuses_overwrite() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "x.txt").write_text("old\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute(
        "write_file",
        {"path": "x.txt", "content": "new\n", "mode": "create"},
    )
    assert out.startswith("Error")
    assert "exists" in out.lower()
    assert (work / "x.txt").read_text(encoding="utf-8") == "old\n"


def test_write_file_append() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "a.txt").write_text("hi\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute(
        "write_file",
        {"path": "a.txt", "content": "there\n", "mode": "append"},
    )
    assert "Appended" in out
    assert (work / "a.txt").read_text(encoding="utf-8") == "hi\nthere\n"


def test_write_file_overwrite_default() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    (work / "b.txt").write_text("old\n", encoding="utf-8")
    sandbox.bind_agent("ops")
    out = execute("write_file", {"path": "b.txt", "content": "new\n"})
    assert "Wrote" in out or "Created" in out
    assert (work / "b.txt").read_text(encoding="utf-8") == "new\n"

"""read_file / write_file tools: success, jail escape, error strings."""

from __future__ import annotations

import pytest

from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def work_bound() -> None:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    return work


def test_read_write_schemas_loaded() -> None:
    names = {t["function"]["name"] for t in get_openai_tools()}
    assert "read_file" in names
    assert "write_file" in names


def test_write_then_read_roundtrip(work_bound) -> None:
    wrote = execute("write_file", {"path": "notes/hello.txt", "content": "hi from tomo"})
    assert wrote.startswith("Wrote")
    assert execute("read_file", {"path": "notes/hello.txt"}) == "hi from tomo"
    assert (work_bound / "notes" / "hello.txt").read_text(encoding="utf-8") == "hi from tomo"


def test_read_missing_file_is_error(work_bound) -> None:
    result = execute("read_file", {"path": "missing.txt"})
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_read_rejects_path_escape(work_bound) -> None:
    result = execute("read_file", {"path": "../SOUL.md"})
    assert result.startswith("Error")
    assert "escape" in result.lower() or "absolute" in result.lower()


def test_write_rejects_absolute_path(work_bound) -> None:
    result = execute("write_file", {"path": "/etc/passwd", "content": "nope"})
    assert result.startswith("Error")
    assert "absolute" in result.lower()


def test_write_rejects_dotdot_escape(work_bound) -> None:
    result = execute("write_file", {"path": "../../outside.txt", "content": "nope"})
    assert result.startswith("Error")
    assert "escape" in result.lower()


def test_read_missing_path_arg_is_error(work_bound) -> None:
    assert execute("read_file", {}).startswith("Error")


def test_write_missing_content_is_error(work_bound) -> None:
    assert execute("write_file", {"path": "a.txt"}).startswith("Error")

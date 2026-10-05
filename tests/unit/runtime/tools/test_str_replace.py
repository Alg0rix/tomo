"""str_replace tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    store.rebind(tmp_path / "str-replace.db")
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def admin_fs():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_str_replace_happy_path(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("hello world", encoding="utf-8")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "world", "new_string": "tomo"},
    )
    assert "Replaced" in result
    assert (work / "note.txt").read_text(encoding="utf-8") == "hello tomo"


def test_str_replace_missing_old_is_error(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("hello", encoding="utf-8")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "missing", "new_string": "x"},
    )
    assert result.startswith("Error")
    assert "not found" in result.lower()


def test_str_replace_non_unique_is_error(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
    result = execute(
        "str_replace",
        {"path": "note.txt", "old_string": "aa", "new_string": "bb"},
    )
    assert result.startswith("Error")
    assert "unique" in result.lower() or "2" in result


def test_str_replace_count_all(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
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


def test_str_replace_count_two(admin_fs) -> None:
    work = admin_fs
    (work / "note.txt").write_text("aa aa", encoding="utf-8")
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


def test_str_replace_escape_is_error(admin_fs) -> None:
    result = execute(
        "str_replace",
        {"path": "../x", "old_string": "a", "new_string": "b"},
    )
    assert result.startswith("Error")


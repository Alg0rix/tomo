"""search_files tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry
from app.services import store
from tests.fakes.access import owned_admin_scope


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path) -> None:
    # Isolate from other tests that rebind the global store / assign workplaces.
    store.rebind(tmp_path / "search-files.db")
    reset_registry()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


@pytest.fixture()
def admin_fs():
    # Explicit owned Admin unrestricted context (real policy grant + ack).
    # Host tools are fenced to the session workplace root, not TOMO_WORK.
    with owned_admin_scope() as (_ctx, root):
        yield root


def test_search_files_finds_substring(admin_fs) -> None:
    work = admin_fs
    (work / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    result = execute("search_files", {"pattern": "hello"})
    assert "a.py:1:" in result
    assert "hello" in result


def test_search_files_glob_filters(admin_fs) -> None:
    work = admin_fs
    (work / "a.py").write_text("needle\n", encoding="utf-8")
    (work / "b.txt").write_text("needle\n", encoding="utf-8")
    result = execute("search_files", {"pattern": "needle", "glob": "*.py"})
    assert "a.py" in result
    assert "b.txt" not in result


def test_search_files_no_match(admin_fs) -> None:
    work = admin_fs
    (work / "a.py").write_text("ok\n", encoding="utf-8")
    result = execute("search_files", {"pattern": "zzz_missing"})
    assert result.startswith("No matches")


def test_search_files_bad_regex_is_error(admin_fs) -> None:
    # regex is default true
    result = execute("search_files", {"pattern": "["})
    assert result.startswith("Error")


def test_search_files_regex_alternation_by_default(admin_fs) -> None:
    work = admin_fs
    (work / "wa.txt").write_text("hello WhatsApp world\n", encoding="utf-8")
    result = execute(
        "search_files",
        {"pattern": "WhatsApp|whatsapp", "path": ".", "output_mode": "content"},
    )
    assert "wa.txt" in result
    assert "WhatsApp" in result


def test_search_files_fixed_string_opt_out(admin_fs) -> None:
    work = admin_fs
    (work / "wa.txt").write_text("hello WhatsApp world\n", encoding="utf-8")
    (work / "lit.txt").write_text("literal WhatsApp|whatsapp here\n", encoding="utf-8")
    result = execute(
        "search_files",
        {
            "pattern": "WhatsApp|whatsapp",
            "regex": False,
            "output_mode": "content",
        },
    )
    assert "lit.txt" in result
    assert "wa.txt" not in result


def test_search_files_by_filename(admin_fs) -> None:
    work = admin_fs
    (work / "app.py").write_text("x\n", encoding="utf-8")
    (work / "readme.md").write_text("y\n", encoding="utf-8")
    result = execute(
        "search_files", {"pattern": "*.py", "target": "files"}
    )
    assert "app.py" in result
    assert "readme.md" not in result


def test_search_files_count_mode(admin_fs) -> None:
    work = admin_fs
    (work / "a.txt").write_text("foo\nbar foo\n", encoding="utf-8")
    result = execute(
        "search_files",
        {"pattern": "foo", "output_mode": "count"},
    )
    assert "a.txt" in result
    assert "2" in result


def test_search_files_context(admin_fs) -> None:
    work = admin_fs
    (work / "c.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = execute(
        "search_files",
        {"pattern": "two", "context": 1},
    )
    assert "c.txt:1:" in result
    assert "c.txt:2:>" in result or "c.txt:2:" in result
    assert "three" in result

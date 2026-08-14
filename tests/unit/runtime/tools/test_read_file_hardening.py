"""read_file hardening: ceilings, informative notes, aliasing, resilience, dedup."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import home
from app.runtime.tools import read_file, sandbox
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_registry()
    sandbox.reset_agent()
    read_file._dedup_cache.clear()
    yield
    sandbox.reset_agent()
    reset_registry()
    read_file._dedup_cache.clear()


@pytest.fixture()
def work_bound() -> Path:
    work = home.agent_work_dir("ops")
    work.mkdir(parents=True, exist_ok=True)
    sandbox.bind_agent("ops")
    return work


def test_empty_file_is_informative(work_bound) -> None:
    (work_bound / "empty.txt").write_text("", encoding="utf-8")
    out = execute("read_file", {"path": "empty.txt"})
    assert "is empty" in out
    assert not out.startswith("Error")


def test_offset_past_eof_is_note_not_error(work_bound) -> None:
    (work_bound / "small.txt").write_text("a\nb\n", encoding="utf-8")
    out = execute("read_file", {"path": "small.txt", "offset": 900})
    assert out.startswith("Note:")
    assert not out.startswith("Error")
    assert "900" in out


def test_fractional_offset_rejected_not_floored(work_bound) -> None:
    (work_bound / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")
    out = execute("read_file", {"path": "f.txt", "offset": 1.5})
    assert out.startswith("Error")
    assert "fractional" in out.lower()


def test_non_numeric_offset_rejected(work_bound) -> None:
    (work_bound / "f2.txt").write_text("a\n", encoding="utf-8")
    out = execute("read_file", {"path": "f2.txt", "offset": "2abc"})
    assert out.startswith("Error")


def test_path_alias_file_path_accepted(work_bound) -> None:
    (work_bound / "aliased.txt").write_text("hi\n", encoding="utf-8")
    out = execute("read_file", {"file_path": "aliased.txt"})
    assert "1|hi" in out


def test_mega_line_is_clamped(work_bound) -> None:
    (work_bound / "mega.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    out = execute("read_file", {"path": "mega.txt"})
    assert "line truncated" in out
    assert len(out) < 4000


def test_curly_quote_spelling_auto_retry(work_bound) -> None:
    (work_bound / "it’s.txt").write_text("hi\n", encoding="utf-8")
    out = execute("read_file", {"path": "it's.txt"})
    assert "auto-corrected" in out
    assert "1|hi" in out


def test_typo_does_not_auto_resolve_stays_suggestion(work_bound) -> None:
    (work_bound / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    out = execute("read_file", {"path": "config.yaaml"})
    assert out.startswith("Error")
    assert "Did you mean" in out
    assert "config.yaml" in out


def test_dedup_stub_on_repeated_identical_read(work_bound) -> None:
    (work_bound / "dup.txt").write_text("same\n", encoding="utf-8")
    first = execute("read_file", {"path": "dup.txt"})
    assert "1|same" in first
    second = execute("read_file", {"path": "dup.txt"})
    assert "unchanged since last read" in second
    # Consumed on hit: third identical call re-reads in full again.
    third = execute("read_file", {"path": "dup.txt"})
    assert "1|same" in third


def test_blocked_device_paths() -> None:
    assert read_file._is_blocked_device_path(Path("/dev/zero"))
    assert read_file._is_blocked_device_path(Path("/dev/urandom"))
    assert read_file._is_blocked_device_path(Path("/dev/stdin"))
    assert read_file._is_blocked_device_path(Path("/proc/123/fd/5"))
    assert not read_file._is_blocked_device_path(Path("/tmp/ok.txt"))

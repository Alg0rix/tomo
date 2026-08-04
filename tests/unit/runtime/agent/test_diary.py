"""Diary extract / synthesize."""

from __future__ import annotations

from app.runtime.agent.learning.diary import (
    derive_diary,
    extract_diary_line,
    synthesize_diary_from_actions,
)


def test_extract_diary_line() -> None:
    note = "Done.\nDiary: Noted that you prefer concise answers.\n"
    assert "concise" in extract_diary_line(note)


def test_extract_inline() -> None:
    assert extract_diary_line("Diary: Hello world") == "Hello world"


def test_synthesize() -> None:
    s = synthesize_diary_from_actions(["memory: added user entry", "manage_skill: patched x"])
    assert "memory" in s
    assert s.startswith("Recorded:")


def test_derive_idle_empty() -> None:
    assert derive_diary(saved=False, note="Nothing to save.", actions=[]) == ""


def test_derive_fallback() -> None:
    d = derive_diary(saved=True, note="ok", actions=["memory: user"])
    assert "memory" in d

"""Session turn lock — one concurrent turn per session."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def test_session_turn_lock_exclusive(tmp_path: Path) -> None:
    store.rebind(tmp_path / "busy_turn.db")
    assert store.try_begin_session_turn("sess_a") is True
    assert store.is_session_turn_active("sess_a") is True
    assert store.try_begin_session_turn("sess_a") is False
    assert store.try_begin_session_turn("sess_b") is True
    store.end_session_turn("sess_a")
    assert store.is_session_turn_active("sess_a") is False
    assert store.try_begin_session_turn("sess_a") is True
    store.end_session_turn("sess_a")
    store.end_session_turn("sess_b")


def test_session_turn_lock_cleared_on_rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "busy_turn2.db")
    assert store.try_begin_session_turn("s1") is True
    store.rebind(tmp_path / "busy_turn3.db")
    assert store.is_session_turn_active("s1") is False
    assert store.try_begin_session_turn("s1") is True

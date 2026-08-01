"""Unit tests for path jail helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.paths import ensure_under, try_under


def test_ensure_under_relative(tmp_path: Path) -> None:
    child = ensure_under(tmp_path, "a/b.txt")
    assert child == (tmp_path / "a" / "b.txt").resolve()


def test_ensure_under_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ensure_under(tmp_path, "../outside")


def test_try_under_none_on_escape(tmp_path: Path) -> None:
    assert try_under(tmp_path, "../x") is None

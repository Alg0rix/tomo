"""Tests for outside_grant + jail_path interaction."""

from __future__ import annotations

from pathlib import Path

from app.runtime.permissions.grants import (
    reset_outside_grant,
    set_outside_grant,
)
from app.runtime.tools.sandbox import jail_path


def test_jail_rejects_without_grant(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    err = jail_path(root, str(outside))
    assert isinstance(err, str) and err.startswith("Error")


def test_jail_allows_with_path_grant(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = (tmp_path / "outside-file").resolve()
    target.write_text("x", encoding="utf-8")
    tok = set_outside_grant(frozenset({target}))
    try:
        got = jail_path(root, str(target))
        assert got == target
    finally:
        reset_outside_grant(tok)


def test_jail_allows_star_grant(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = (tmp_path / "anywhere").resolve()
    target.write_text("y", encoding="utf-8")
    tok = set_outside_grant("*")
    try:
        assert jail_path(root, str(target)) == target
    finally:
        reset_outside_grant(tok)


def test_grant_cleared_after_reset(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    target = (tmp_path / "gone").resolve()
    target.write_text("z", encoding="utf-8")
    tok = set_outside_grant(frozenset({target}))
    reset_outside_grant(tok)
    err = jail_path(root, str(target))
    assert isinstance(err, str) and err.startswith("Error")

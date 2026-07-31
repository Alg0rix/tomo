"""Tests for permission assess / patterns / escape."""

from __future__ import annotations

from pathlib import Path

from app.runtime.permissions.assess import assess


def test_ls_in_root_clean(tmp_path: Path) -> None:
    a = assess("bash", {"command": "ls"}, tmp_path)
    assert a.findings == []


def test_rm_rf_root_hardline(tmp_path: Path) -> None:
    a = assess("bash", {"command": "rm -rf /"}, tmp_path)
    assert a.has_hardline()


def test_read_file_escape(tmp_path: Path) -> None:
    a = assess(
        "read_file",
        {"path": str(Path.home() / ".tomo" / "x")},
        tmp_path,
    )
    assert any(f.kind == "escape" for f in a.findings)


def test_user_deny_glob(tmp_path: Path) -> None:
    a = assess(
        "bash",
        {"command": "git push --force origin main"},
        tmp_path,
        deny_globs=["git push --force*"],
    )
    assert a.has_user_deny()


def test_bash_home_escape(tmp_path: Path) -> None:
    a = assess("bash", {"command": "ls ~/.tomo"}, tmp_path)
    assert any(f.kind == "escape" for f in a.findings)


def test_recursive_rm_dangerous_not_hardline(tmp_path: Path) -> None:
    target = tmp_path / "subdir"
    target.mkdir()
    a = assess("bash", {"command": f"rm -rf {target}"}, tmp_path)
    assert not a.has_hardline()
    assert any(f.kind == "dangerous" for f in a.findings)

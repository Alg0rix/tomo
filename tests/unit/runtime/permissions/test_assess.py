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


def test_mcp_tool_call_flags_external(tmp_path: Path) -> None:
    a = assess("mcp__github__create_issue", {"title": "x"}, tmp_path)
    assert any(f.kind == "external" for f in a.findings)
    assert "mcp__github__create_issue" in a.allowlist_keys()[0] or any(
        "mcp__github__create_issue" in k for k in a.allowlist_keys()
    )


def test_mcp_tool_call_ignores_annotations_for_finding_kind(tmp_path: Path) -> None:
    # Annotations aren't part of assess()'s inputs at all — the finding kind
    # is derived purely from the ``mcp__`` name prefix, never from server-
    # declared safety hints.
    a = assess(
        "mcp__github__create_issue",
        {"title": "x", "annotations": {"readOnlyHint": True}},
        tmp_path,
    )
    assert any(f.kind == "external" for f in a.findings)


def test_non_mcp_tool_has_no_external_finding(tmp_path: Path) -> None:
    a = assess("read_file", {"path": "notes.txt"}, tmp_path)
    assert not any(f.kind == "external" for f in a.findings)

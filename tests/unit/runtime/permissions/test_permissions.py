"""Consolidated tests (merged from: test_gate.py, test_assess.py).
- test_gate.py: Gate tests for manual / off modes.
- test_assess.py: Tests for permission assess / patterns / escape.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from app.runtime.permissions.gate import decide
from app.runtime.permissions.modes import clear_session_modes, set_session_mode
from app.runtime.permissions.assess import assess


# --- from test_gate.py ---
@pytest.fixture(autouse=True)
def _clean_modes() -> None:
    clear_session_modes()
    yield
    clear_session_modes()


@pytest.mark.asyncio
async def test_off_allows_escape(tmp_path: Path) -> None:
    set_session_mode("s1", "off")
    d = await decide(
        "read_file",
        {"path": str(Path.home() / ".tomo")},
        work_root=tmp_path,
        session_id="s1",
    )
    assert d.allowed
    assert d.grant == "*"


@pytest.mark.asyncio
async def test_hardline_blocks_in_off(tmp_path: Path) -> None:
    set_session_mode("s1", "off")
    d = await decide(
        "bash",
        {"command": "rm -rf /"},
        work_root=tmp_path,
        session_id="s1",
    )
    assert not d.allowed
    assert "hardline" in (d.message or "").lower()


@pytest.mark.asyncio
async def test_manual_escape_blocks_without_waiter(tmp_path: Path) -> None:
    set_session_mode("s1", "manual")
    d = await decide(
        "bash",
        {"command": "ls ~/.tomo"},
        work_root=tmp_path,
        session_id="s1",
    )
    assert not d.allowed
    assert d.needs_hitl
    assert "approval required" in (d.message or "").lower()


@pytest.mark.asyncio
async def test_manual_hitl_once_allows(tmp_path: Path) -> None:
    set_session_mode("s1", "manual")

    async def _wait(**_kwargs):
        return "once"

    d = await decide(
        "bash",
        {"command": "ls ~/.tomo"},
        work_root=tmp_path,
        session_id="s1",
        hitl_wait=_wait,
    )
    assert d.allowed
    assert d.grant is not None
    assert not d.needs_hitl


@pytest.mark.parametrize('action', ['kill', ' KILL ', 'Close-Monitoring', ' close-monitoring '])
async def test_process_mutations_require_current_permission(tmp_path, action):
    set_session_mode('process-permission', 'manual')
    decision = await decide('process', {'action': action, 'id': ' job_abc '},
                            work_root=tmp_path, session_id='process-permission')
    assert not decision.allowed and decision.needs_hitl
    assert decision.findings[0].key == f'process:{action.strip().lower()}:job_abc'


async def test_process_exact_deny_cannot_be_bypassed_by_spacing(tmp_path, monkeypatch):
    from app.runtime.permissions import gate

    set_session_mode('process-permission', 'off')
    monkeypatch.setattr(gate, '_deny_globs', lambda: ['process:kill:job_abc'])
    decision = await decide('process', {'action': ' KILL ', 'id': ' job_abc '},
                            work_root=tmp_path, session_id='process-permission')
    assert not decision.allowed and any(f.kind == 'user_deny' for f in decision.findings)


# --- from test_assess.py ---
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



"""Gate tests for manual / off modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime.permissions.gate import decide
from app.runtime.permissions.modes import clear_session_modes, set_session_mode


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
